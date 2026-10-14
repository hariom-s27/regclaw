"""The R11 E1 grounding checks. Response retries live in extract.py."""
from collections import Counter
import copy
import json
from pathlib import Path

import jsonschema

from .normalise import surface
from .stdref import is_standard

SCHEMA = json.loads(Path(__file__).with_name('schema.json').read_text(encoding='utf-8'))
ENTITY_TYPES = tuple(k for k in SCHEMA['properties']['entities']['required'] if k != 'keywords')
ENUMS = {k: SCHEMA['properties']['entities']['properties'][k]['items']['properties']['canonical'].get('enum') for k in ENTITY_TYPES}
BLOCKLIST = {'device', 'devices', 'material', 'materials', 'test', 'tests', 'requirement', 'requirements', 'product', 'products'}


def valid_stop_reason(reason, mode='structured'):
    return reason == 'end_turn' or mode == 'strict_tool' and reason == 'tool_use'


def check_response(data, clause, normaliser):
    # Validate the structure before traversing it, while permitting enum case coercion.
    shape = copy.deepcopy(SCHEMA)
    for kind in ENTITY_TYPES:
        shape['properties']['entities']['properties'][kind]['items']['properties']['canonical'].pop('enum', None)
    jsonschema.validate(data, shape)
    data = copy.deepcopy(data)
    rejects, review, grounding = [], [], {k: {'total': 0, 'rejected': 0} for k in ENTITY_TYPES}
    mentions = {}
    for kind in ENTITY_TYPES:
        kept, seen = [], set()
        for e in data['entities'][kind]:
            grounding[kind]['total'] += 1
            mention, evidence = surface(e['mention']), surface(e['evidence'])
            reason = None
            if ENUMS[kind]:
                canonical = e['canonical'].casefold()
                if canonical not in ENUMS[kind]:
                    reason = 'enum_invalid'
                else:
                    e['canonical'] = canonical
            if not reason and (not mention or mention not in surface(clause)):
                reason = 'mention_grounding'
            if not reason and (not evidence or evidence not in surface(clause) or mention not in evidence):
                reason = 'evidence_grounding'
            if not reason and mention in BLOCKLIST:
                reason = 'blocklist'
            if not reason and is_standard(e['mention']):
                reason = 'standard_as_entity'
            if reason:
                rejects.append({'type': kind, 'mention': e['mention'], 'reason': reason})
                grounding[kind]['rejected'] += reason in ('mention_grounding', 'evidence_grounding')
                continue
            if not normaliser.legitimate(e['mention'], e['canonical'], kind, clause):
                review.append({'type': kind, 'candidate': e['mention'], 'existing': e['canonical'],
                               'ratio': '', 'accept': '', 'reason': 'unsupported_expansion'})
            key = surface(e['canonical']).replace('_', ' ')
            if key in seen:
                rejects.append({'type': kind, 'mention': e['mention'], 'reason': 'duplicate'})
                continue
            seen.add(key)
            if mention in mentions and mentions[mention] != kind:
                review.append({'type': kind, 'candidate': e['mention'], 'existing': mentions[mention],
                               'ratio': '', 'accept': '', 'reason': 'cross_type_collision'})
            mentions[mention] = kind
            kept.append(e)
        data['entities'][kind] = kept
    names = {surface(e['canonical']).replace('_', ' ') for k in ENTITY_TYPES for e in data['entities'][k]}
    data['entities']['keywords'] = list(dict.fromkeys(surface(k) for k in data['entities']['keywords'] if surface(k) and surface(k) not in names))[:8]
    jsonschema.validate(data, SCHEMA)
    return data, rejects, review, grounding


def drift(current, previous):
    alerts = []
    for kind, now in current.items():
        if kind not in previous:
            continue
        for metric in ('mean', 'empty_rate'):
            before = previous[kind][metric]
            after = now[metric]
            if (before == 0 and after != 0) or (before != 0 and abs(after-before)/abs(before) > .25):
                alerts.append({'type': kind, 'metric': metric, 'previous': before, 'current': after})
    return alerts
