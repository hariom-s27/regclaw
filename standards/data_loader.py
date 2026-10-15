"""Local, schema-checked rule data. Hash exact YAML bytes for provenance."""
import hashlib
from pathlib import Path
import jsonschema
import yaml

ENDPOINTS = ['Cyto', 'Sens', 'Irr', 'AST', 'MMP', 'SubA', 'Geno', 'Impl', 'Hemo', 'Chr', 'Carc']
PROVENANCE = {'type': 'object', 'additionalProperties': False,
              'required': ['doc', 'locator', 'source', 'verified_by_human'], 'properties': {
                  'doc': {'const': 'fda-iso10993-1'}, 'locator': {'type': 'string', 'minLength': 1},
                  'source': {'const': 'FDA web page via R9; PDF cross-check below'},
                  'verified_by_human': {'const': False}}}


def obj(properties, required=None):
    return {'type': 'object', 'properties': properties, 'additionalProperties': False,
            'required': list(properties) if required is None else required}


CELL = obj({'consider': {'type': 'boolean'}, 'provenance': PROVENANCE,
            'footnote': {'enum': ['extracorporeal', None]}})
ROW = obj({'nature': {'enum': ['surface', 'externally_communicating', 'implant']},
           'site': {'type': 'string'}, 'duration': {'enum': ['A', 'B', 'C']},
           'cells': obj({ep: CELL for ep in ENDPOINTS})})
MATRIX_SCHEMA = obj({'version': {'const': 1}, 'rows': {'type': 'array', 'minItems': 24, 'maxItems': 24, 'items': ROW},
                     'footnotes': obj({'extracorporeal': {'type': 'string'}})})
ANCHOR_SCHEMA = obj({'version': {'const': 1}, 'other_rows_unverified': {'const': True},
                     'indirect_path_mapping_unverified': {'const': True},
                     'rows': {'type': 'array', 'minItems': 5, 'maxItems': 5, 'items': obj({
                         'contact': {'type': 'string'}, 'duration': {'enum': ['A', 'B', 'C']},
                         'nature': {'type': ['string', 'null']},
                         'endpoints': {'type': 'array', 'items': {'type': 'string'}},
                         'source': {'const': 'PMDA examples via R9'}, 'verified_by_human': {'const': False}})}})
PART_SCHEMA = obj({'version': {'const': 1}, 'parts': {'type': 'array', 'minItems': 1, 'items': obj({
    'part': {'type': 'string'}, 'edition': {'type': 'string'}, 'edition_verified': {'type': 'boolean'},
    'endpoints': {'type': 'array', 'minItems': 1, 'items': {'type': 'string'}}})}})
LABEL_SCHEMA = obj({'version': {'const': 1}, 'labels': obj({
    label: {'type': 'string', 'minLength': 1} for label in ['mandatory', 'recommended', 'not_applicable']}),
    'caveat': {'type': 'string', 'minLength': 1}, 'banned_phrases': {'type': 'array', 'minItems': 4, 'items': {'type': 'string'}}})
FILES = {'matrix': ('fda2023_attachment_a.v1.yaml', MATRIX_SCHEMA),
         'anchors': ('iso2025_anchor_rows.v1.yaml', ANCHOR_SCHEMA),
         'part_map': ('endpoint_to_part.v1.yaml', PART_SCHEMA),
         'labels': ('labels.v1.yaml', LABEL_SCHEMA)}


def validate(data):
    for key, (_, schema) in FILES.items():
        jsonschema.Draft202012Validator(schema).validate(data[key])
    rows = data['matrix']['rows']
    keys = [(r['nature'], r['site'], r['duration']) for r in rows]
    if len(set(keys)) != 24:
        raise ValueError('Duplicate matrix rows')
    expected = {(n, s, d) for n, s in [
        ('surface', 'intact_skin'), ('surface', 'mucosal_membrane'), ('surface', 'breached_compromised_surface'),
        ('externally_communicating', 'blood_path_indirect'), ('externally_communicating', 'tissue_bone_dentin'),
        ('externally_communicating', 'circulating_blood'), ('implant', 'tissue_bone'), ('implant', 'blood')]
        for d in 'ABC'}
    if set(keys) != expected:
        raise ValueError('Missing or unexpected matrix row')
    for r in rows:
        for ep, cell in r['cells'].items():
            should_have_note = (r['nature'], r['site'], r['duration'], ep) == (
                'externally_communicating', 'circulating_blood', 'A', 'Geno')
            if (cell['footnote'] == 'extracorporeal') != should_have_note:
                raise ValueError('Extracorporeal footnote missing or in wrong cell')
    parts = data['part_map']['parts']
    if len({p['part'] for p in parts}) != len(parts):
        raise ValueError('Duplicate part map entry')
    mapped = {ep for p in parts for ep in p['endpoints']}
    if not set(ENDPOINTS) <= mapped:
        raise ValueError('Unmapped endpoint')
    for p in parts:
        if (p['edition'] == 'UNVERIFIED') == p['edition_verified']:
            raise ValueError('Edition verification flag contradicts edition')
    return data


def load_data(folder=None):
    folder = Path(folder) if folder else Path(__file__).with_name('data')
    data, versions = {}, {}
    for key, (name, _) in FILES.items():
        raw = (folder/name).read_bytes()
        data[key] = yaml.safe_load(raw)
        versions[name] = hashlib.sha256(raw).hexdigest()
    validate(data)
    return {**data, 'data_version': versions}
