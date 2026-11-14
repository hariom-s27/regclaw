"""S10 eval-set FILE FORMATS (R23): device-profile sets and gold labels.

Formats only; real profiles and gold are written later by a human. See eval/s10/README.md.
Gold is keyed by ISO PART (the unit the tool outputs), never by endpoint, and carries a
source pointer (doc code + clause id) but NO clause text (schema forbids extra fields).
"""
from pathlib import Path

import jsonschema
import yaml

from standards.data_loader import load_data, obj
from standards.engine import Context, rule_02
from standards.profile import DeviceProfile

PROFILES_FORMAT = 's10-eval-profiles.v1'
GOLD_FORMAT = 's10-eval-gold.v1'
LABELS = ['not_applicable', 'recommended', 'mandatory']
STRATA = [f'S{i}' for i in range(1, 9)] + ['trap']
SPLITS = ['dev', 'holdout']
FLAGS = ['ethylene_oxide', 'absorbable', 'nano', 'repeat_use', 'multi_component', 'attachment_g_material']
# CLAUDE.md data contract: document codes.
DOC_CODES = ['fda-iso10993-1', 'fda-sterility-510k', 'fda-chem-analysis', 'fda-nitinol', 'fda-knee', 'eu-mdr',
             'imdrf-n47', 'in-ep', 'in-mdr', 'epa-63o', 'who-decon']
NATURES = ['surface', 'externally_communicating', 'implant']
SITES = ['intact_skin', 'mucosal_membrane', 'breached_compromised_surface', 'blood_path_indirect',
         'tissue_bone_dentin', 'circulating_blood', 'tissue_bone', 'blood']
ID = {'type': 'string', 'pattern': r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$'}

PROFILE_ENTRY = obj({
    'id': ID,
    'stratum': {'enum': STRATA},
    'split': {'enum': SPLITS},
    'contact_category': {'enum': [f'{n}/{s}' for n in NATURES for s in SITES]},
    'duration': {'enum': ['A', 'B', 'C']},
    'flags': {'type': 'array', 'uniqueItems': True, 'items': {'enum': FLAGS}},
    'comment': {'type': 'string', 'maxLength': 500},
    'profile': {'type': 'object'},           # DeviceProfile.v1, validated by pydantic below
}, required=['id', 'stratum', 'split', 'contact_category', 'duration', 'flags', 'profile'])
PROFILE_SET_SCHEMA = obj({'format': {'const': PROFILES_FORMAT}, 'split': {'enum': SPLITS},
                          'profiles': {'type': 'array', 'minItems': 1, 'items': PROFILE_ENTRY}})
GOLD_ENTRY = obj({
    'profile_id': ID,
    'item': {'type': 'string', 'minLength': 1, 'maxLength': 64},   # ISO part, e.g. "ISO 10993-5"
    'label': {'enum': LABELS},
    'source': obj({'doc': {'enum': DOC_CODES},
                   'clause_id': {'type': 'string', 'minLength': 3, 'maxLength': 120}}),
})
GOLD_SCHEMA = obj({'format': {'const': GOLD_FORMAT}, 'split': {'enum': SPLITS},
                   'labels': {'type': 'array', 'minItems': 1, 'items': GOLD_ENTRY}})
# S10b-04: practice (decoy) profiles for labeller training. Separate format, split fixed to
# "practice", no stratum, no comment. The dev/holdout schemas above are unchanged.
PRACTICE_FORMAT = 's10-practice-profiles.v1'
PRACTICE_SPLIT = 'practice'
PRACTICE_ENTRY = obj({
    'id': ID,
    'split': {'const': PRACTICE_SPLIT},
    'contact_category': PROFILE_ENTRY['properties']['contact_category'],
    'duration': PROFILE_ENTRY['properties']['duration'],
    'flags': PROFILE_ENTRY['properties']['flags'],
    'profile': {'type': 'object'},           # DeviceProfile.v1, validated by pydantic below
})
PRACTICE_SET_SCHEMA = obj({'format': {'const': PRACTICE_FORMAT}, 'split': {'const': PRACTICE_SPLIT},
                           'profiles': {'type': 'array', 'minItems': 1, 'items': PRACTICE_ENTRY}})


class FormatError(ValueError):
    """An eval file breaks the S10 eval format."""


def read_yaml(path):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8'))


def derived_flags(profile: DeviceProfile) -> set:
    comps = profile.components
    mats = [m for c in comps for m in c.materials]
    flags = set()
    if any(c.sterilization == 'ethylene_oxide' for c in comps):
        flags.add('ethylene_oxide')
    if any(m.absorbable_or_degradable or m.in_situ_polymerizing for m in mats):
        flags.add('absorbable')
    if any(m.submicron_or_nano for m in mats):
        flags.add('nano')
    if any(c.exposure.pattern == 'intermittent_repeat' for c in comps):
        flags.add('repeat_use')
    if len(comps) > 1:
        flags.add('multi_component')
    # Same trigger as engine.rule_11 (Attachment G note).
    if any(c.contact_site == 'intact_skin' and any(m.material_class in {'polymer', 'hydrogel_adhesive'}
                                                   for m in c.materials) for c in comps):
        flags.add('attachment_g_material')
    return flags


def derived_duration(profile: DeviceProfile, data) -> str:
    """Longest engine-derived duration class (engine.rule_02) over contacting components."""
    classes = []
    for comp in profile.components:
        if comp.contact_nature == 'non_contact':
            continue
        c = Context(profile, comp, data)
        rule_02(c)
        classes.append(c.duration)
    return max(classes) if classes else 'A'


def check_profile_entry(entry, data):
    try:
        profile = DeviceProfile.model_validate(entry['profile'])
    except Exception as exc:  # pydantic ValidationError
        raise FormatError(f"{entry['id']}: invalid DeviceProfile.v1: {exc}") from exc
    if profile.framework != 'FDA_2023_ATTACHMENT_A':
        raise FormatError(f"{entry['id']}: eval profiles must use framework FDA_2023_ATTACHMENT_A "
                          f'(got {profile.framework}); see eval/s10/README.md')
    cats = {f'{c.contact_nature}/{c.contact_site}' for c in profile.components}
    if entry['contact_category'] not in cats:
        raise FormatError(f"{entry['id']}: contact_category {entry['contact_category']} matches no component {sorted(cats)}")
    duration = derived_duration(profile, data)
    if entry['duration'] != duration:
        raise FormatError(f"{entry['id']}: declared duration {entry['duration']} != engine-derived {duration}")
    flags = derived_flags(profile)
    if set(entry['flags']) != flags:
        raise FormatError(f"{entry['id']}: declared flags {sorted(entry['flags'])} != derived {sorted(flags)}")
    return profile


def validate_profile_set(doc, data=None):
    """Schema + per-entry consistency. Returns {id: entry}."""
    try:
        jsonschema.Draft202012Validator(PROFILE_SET_SCHEMA).validate(doc)
    except jsonschema.ValidationError as exc:
        raise FormatError(f'profile set: {exc.message}') from exc
    data = data or load_data()
    out = {}
    for entry in doc['profiles']:
        if entry['id'] in out:
            raise FormatError(f"duplicate profile id {entry['id']}")
        if entry['split'] != doc['split']:
            raise FormatError(f"{entry['id']}: split {entry['split']} differs from file split {doc['split']}")
        check_profile_entry(entry, data)
        out[entry['id']] = entry
    return out


def validate_practice_set(doc, data=None):
    """S10b-04 practice set: schema + the same per-entry checks as eval profiles. Returns {id: entry}."""
    try:
        jsonschema.Draft202012Validator(PRACTICE_SET_SCHEMA).validate(doc)
    except jsonschema.ValidationError as exc:
        raise FormatError(f'practice set: {exc.message}') from exc
    data = data or load_data()
    out = {}
    for entry in doc['profiles']:
        if entry['id'] in out:
            raise FormatError(f"duplicate profile id {entry['id']}")
        check_profile_entry(entry, data)
        out[entry['id']] = entry
    return out


def validate_gold(doc, profiles, data=None):
    """Schema + referential checks against the profile set. Returns {(profile_id, item): entry}."""
    try:
        jsonschema.Draft202012Validator(GOLD_SCHEMA).validate(doc)
    except jsonschema.ValidationError as exc:
        raise FormatError(f'gold: {exc.message}') from exc
    data = data or load_data()
    parts = {p['part'] for p in data['part_map']['parts']}
    out = {}
    for entry in doc['labels']:
        pid, item = entry['profile_id'], entry['item']
        if pid not in profiles:
            raise FormatError(f'gold refers to unknown profile {pid}')
        if profiles[pid]['split'] != doc['split']:
            raise FormatError(f'gold split {doc["split"]} differs from profile {pid} split')
        allowed = parts | set(profiles[pid]['profile'].get('device_specific_standards', []))
        if item not in allowed:
            raise FormatError(f'{pid}: gold item {item!r} is not an ISO part of the part map or a declared device-specific standard')
        src = entry['source']
        if not src['clause_id'].startswith(src['doc'] + ':'):
            raise FormatError(f"{pid}/{item}: clause_id {src['clause_id']!r} must start with '{src['doc']}:'")
        if (pid, item) in out:
            raise FormatError(f'duplicate gold cell {pid} x {item}')
        out[(pid, item)] = entry
    return out
