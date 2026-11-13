"""S10-17 cell tags: matrix_settled | always_rule | judgement, one per (profile, item).

Counterfactual tagger. It never edits the locked engine; it imports `evaluate` and `rule_02`
and compares the engine's rule label with a label computed from the FDA 2023 Attachment A
row alone. Order of checks for one cell (first match wins):

1. item is a device-specific standard of the profile          -> judgement
2. item is an always-rule part and a component has contact    -> always_rule
3. the profile has a feature the engine cannot express        -> judgement
4. the part has a matrix column, exactly one component has contact, its matrix row exists,
   and the matrix-only label equals the engine label          -> matrix_settled
5. anything else                                              -> judgement

always_rule parts are the parts whose endpoints are all set to mandatory by engine rule R08
for every contacting component (standards/engine.py, rule_08: Framework, TRA, Chemical, Sample).
Limit: tags follow the engine's rule encoding; an engine mistake is copied into the tag.
"""
import json
from pathlib import Path

from standards.data_loader import ENDPOINTS
from standards.engine import RANK, Context, evaluate, rule_02
from standards.profile import DeviceProfile

TAGS = ('matrix_settled', 'always_rule', 'judgement')
TAGS_FORMAT = 's10-cell-tags.v1'
FRAMEWORK = 'FDA_2023_ATTACHMENT_A'
# engine.rule_08 sets these four endpoints to mandatory whatever the profile says.
ALWAYS_RULE_ENDPOINTS = frozenset({'Framework', 'TRA', 'Chemical', 'Sample'})


class TagError(ValueError):
    """A tags file breaks the s10-cell-tags.v1 format or misses a needed cell."""


def _profile(profile):
    if isinstance(profile, DeviceProfile):
        return profile
    if 'device_name' not in profile and 'profile' in profile:      # an eval-set entry
        profile = profile['profile']
    return DeviceProfile.model_validate(profile)


def always_rule_parts(data):
    return [p['part'] for p in data['part_map']['parts'] if set(p['endpoints']) <= ALWAYS_RULE_ENDPOINTS]


def matrix_parts(data):
    """Parts with at least one endpoint that is a column of the Attachment A matrix."""
    return [p['part'] for p in data['part_map']['parts'] if set(p['endpoints']) & set(ENDPOINTS)]


def contacting(profile):
    return [c for c in profile.components if c.contact_nature != 'non_contact']


def _duration(profile, comp, data):
    c = Context(profile, comp, data)
    rule_02(c)
    return c.duration, bool(c.assumptions)      # an R02 assumption means the exposure was missing


def inexpressible_features(profile, data):
    """Profile features the engine cannot turn into labels (Q3). Empty list = none."""
    found = []
    if any(p != 'adult' for p in profile.intended_population):
        found.append('special_population')
    materials = [m for c in profile.components for m in c.materials]
    if any(m.novel for m in materials):
        found.append('novel_material')
    if any(m.submicron_or_nano for m in materials):
        found.append('nano_material')
    if any(_duration(profile, c, data)[1] for c in contacting(profile)):
        found.append('missing_exposure')
    return found


def matrix_only_labels(profile, comp, data):
    """{part: label} from the component's Attachment A row alone, or None if the row is absent
    or the profile does not use the FDA framework."""
    if profile.framework != FRAMEWORK:
        return None
    duration, _ = _duration(profile, comp, data)
    rows = [r for r in data['matrix']['rows']
            if (r['nature'], r['site'], r['duration']) == (comp.contact_nature, comp.contact_site, duration)]
    if not rows:
        return None
    considered = {ep for ep, cell in rows[0]['cells'].items() if cell['consider']}
    return {p['part']: 'mandatory' if set(p['endpoints']) & considered else 'not_applicable'
            for p in data['part_map']['parts']}


def counterfactual_labels(profile, data):
    """Label per part with NO trigger rule: Attachment A row(s) plus the always-rule parts.
    None when a contacting component has no matrix row. Used by the tagger and its self-check."""
    profile = _profile(profile)
    out = {p['part']: 'not_applicable' for p in data['part_map']['parts']}
    comps = contacting(profile)
    for comp in comps:
        labels = matrix_only_labels(profile, comp, data)
        if labels is None:
            return None
        for part, label in labels.items():
            out[part] = max([out[part], label], key=RANK.get)
    if comps:
        for part in always_rule_parts(data):
            out[part] = 'mandatory'
    return out


def tag_profile(profile, data):
    """{item: tag} for every item the engine returns for this profile."""
    profile = _profile(profile)
    engine = {p['part']: p['label'] for p in evaluate(profile, data)['parts']}
    specifics = set(profile.device_specific_standards)
    comps = contacting(profile)
    always, matrix = set(always_rule_parts(data)), set(matrix_parts(data))
    features = inexpressible_features(profile, data)
    matrix_only = matrix_only_labels(profile, comps[0], data) if len(comps) == 1 else None
    tags = {}
    for item, label in engine.items():
        if item in specifics:
            tags[item] = 'judgement'
        elif item in always and comps:
            tags[item] = 'always_rule'
        elif features:
            tags[item] = 'judgement'
        elif item in matrix and matrix_only is not None and matrix_only[item] == label:
            tags[item] = 'matrix_settled'
        else:
            tags[item] = 'judgement'
    return tags


def build_tags(profiles, data):
    """Tags document for a validated profile set {id: entry}."""
    rows = [dict(profile_id=pid, item=item, tag=tag)
            for pid in sorted(profiles) for item, tag in sorted(tag_profile(profiles[pid]['profile'], data).items())]
    return dict(format=TAGS_FORMAT, tags=rows)


def write_tags(path, doc):
    path = Path(path)
    with path.open('w', encoding='utf-8', newline='\n') as fh:
        fh.write(json.dumps(doc, indent=2, sort_keys=True) + '\n')
    return path


def load_tags(path, required=()):
    """{(profile_id, item): tag}. Every cell in `required` must be tagged."""
    try:
        doc = json.loads(Path(path).read_text(encoding='utf-8'))
    except ValueError as exc:
        raise TagError(f'tags file unreadable: {Path(path).name}') from exc
    if not isinstance(doc, dict) or doc.get('format') != TAGS_FORMAT or not isinstance(doc.get('tags'), list):
        raise TagError(f'tags file must have format {TAGS_FORMAT}')
    out = {}
    for row in doc['tags']:
        if not isinstance(row, dict) or set(row) != {'profile_id', 'item', 'tag'} or row['tag'] not in TAGS:
            raise TagError('each tag row needs profile_id, item and a tag in ' + ', '.join(TAGS))
        key = (row['profile_id'], row['item'])
        if key in out:
            raise TagError(f'duplicate tag for {key[0]} x {key[1]}')
        out[key] = row['tag']
    missing = sorted(set(required) - set(out))
    if missing:
        raise TagError(f'{len(missing)} scored cell(s) have no tag, first: {missing[0][0]} x {missing[0][1]}')
    return out
