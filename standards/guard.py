"""Validate the entire response, enforce the rule floor, and constrain citations."""
import json
import logging
import re
import unicodedata
import jsonschema

from .classifier import LLMResponse, OUTPUT_SCHEMA
from .engine import RANK

log = logging.getLogger(__name__)


def template_reason(label, data):
    return data['labels']['labels'][label]


def guard(rules, response, data, candidates, device_specific_standards=()):
    """Return safe standards even when JSON, stop reason, or model availability fails."""
    events = []
    canonical = {str(c['clause_id']).casefold(): c['clause_id'] for c in candidates}

    def citations(ids, part):
        kept = []
        for cid in ids:
            key = cid.casefold()
            if key in canonical:
                if canonical[key] not in kept:
                    kept.append(canonical[key])
            else:
                events.append(dict(type='unretrieved_clause', part=part, clause_id=cid))
        return kept

    valid, status = False, 'fallback'
    payload = None
    if isinstance(response, LLMResponse) and response.stop_reason == 'end_turn':
        try:
            payload = json.loads(response.data) if isinstance(response.data, str) else response.data
            jsonschema.Draft202012Validator(OUTPUT_SCHEMA).validate(payload)
            valid, status = True, 'ok'
        except (ValueError, TypeError, jsonschema.ValidationError):
            events.append(dict(type='invalid_output'))
    else:
        events.append(dict(type='fallback', stop_reason=getattr(response, 'stop_reason', 'unavailable')))
    allowed = {p['part']: p for p in data['part_map']['parts']}
    allowed.update({name: dict(part=name, edition='UNVERIFIED') for name in device_specific_standards})
    floor = {p['part']: p for p in rules['parts']}
    accepted = {}
    banned = data['labels']['banned_phrases']
    for item in payload['standards'] if valid else []:
        part = item['part']
        if part not in allowed:
            events.append(dict(type='unknown_part', part=part))
            continue
        # Duplicate entries cannot overwrite a stronger decision.
        if part in accepted:
            events.append(dict(type='duplicate_part', part=part))
            if RANK[item['label']] < RANK[accepted[part]['label']]:
                continue
        base = floor.get(part, dict(part=part, edition=allowed[part]['edition'], label='not_applicable',
                                   endpoints={}, rule_trace=[], suggested_route=None, needs_citation=True))
        label = max([base['label'], item['label']], key=RANK.get)
        if item['label'] != base['label']:
            events.append(dict(type='disagreement', part=part, rule_label=base['label'], llm_label=item['label']))
        reason = item['reason']
        if label != item['label']:
            reason = template_reason(label, data)
        cleaned = ' '.join(unicodedata.normalize('NFKC', reason).casefold().split())
        if not reason.strip() or any(re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', cleaned) for phrase in banned):
            events.append(dict(type='banned_wording', part=part))
            reason = template_reason(label, data)
        ids = citations(item['clause_ids'], part)
        accepted[part] = dict(**base, reason=reason, clause_ids=ids,
                              unsupported_by_corpus=item['unsupported_by_corpus'] or not ids,
                              superseded_edition=item['superseded_edition'])
        accepted[part]['label'] = label
    for part, base in floor.items():
        if part not in accepted:
            accepted[part] = dict(**base, reason=template_reason(base['label'], data), clause_ids=[],
                                  unsupported_by_corpus=True, superseded_edition=False)
            if valid:
                events.append(dict(type='disagreement', part=part, rule_label=base['label'], llm_label='omitted'))
    notes = [dict(text=text, clause_ids=[]) for text in rules['notes'] + rules['assumptions']]
    for note in payload['notes'] if valid else []:
        notes.append(dict(text=note['text'], clause_ids=citations(note['clause_ids'], 'note')))
    order = list(dict.fromkeys(device_specific_standards))
    order += sorted(set(accepted) - set(order))
    for event in events:
        log.info('standards guard: %s', event)
    return dict(standards=[accepted[p] for p in order if p in accepted], notes=notes,
                llm_status=status, disagreements=events)
