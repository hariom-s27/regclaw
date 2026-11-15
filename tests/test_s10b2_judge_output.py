"""S10b2 judge output schema (strict-mode shape) and validator, on FAKE results only."""
import copy
import json

import pytest

from eval.s10 import judge_output as JO

ITEMS = ['ISO 10993-1', 'ISO 10993-5', 'ISO 10993-7']
SHEET = dict(id='FAKE-1', items=ITEMS)
STRICT_BANNED = {'pattern', 'minLength', 'maxLength', 'minItems', 'maxItems', 'format', 'uniqueItems',
                 'patternProperties', 'allOf', 'oneOf', 'not', 'if', 'then', 'else', 'default', 'minimum',
                 'maximum', 'const'}
YN = ['eo_sterilised', 'sold_sterile', 'implant', 'blood_lymph_or_csf_contact', 'designed_to_degrade',
      'may_degrade_or_corrode', 'novel_material', 'nanomaterial', 'reproductive_concern_stated',
      'special_population_stated', 'knee_prosthesis']


def good():
    return dict(device_id='FAKE-1',
                checklist=dict(components=[dict(name='part', contact_category='surface/intact_skin', duration='A',
                                                table_row='Surface / Intact skin / A')],
                               **{k: 'no' for k in YN}, material_classes=['polymer']),
                cells=[dict(item=i, reason='Table row marks it → §4.1', label='mandatory', confidence='high',
                            section='§4.1', needs_sme='') for i in ITEMS])


@pytest.fixture(scope='module')
def schema():
    return JO.load_schema()


def walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from walk(v)


def test_schema_is_strict_mode_shaped(schema):
    assert schema['$schema'] == 'https://json-schema.org/draft/2020-12/schema'
    objects = [n for n in walk(schema) if isinstance(n, dict) and n.get('type') == 'object']
    assert len(objects) == 4
    for obj in objects:
        assert obj['additionalProperties'] is False
        assert obj['required'] == list(obj['properties'])
    keys = set().union(*(set(n) for n in walk(schema) if isinstance(n, dict) and 'type' in n))
    assert not keys & STRICT_BANNED


def test_schema_property_order(schema):
    p = schema['properties']
    assert list(p) == ['device_id', 'checklist', 'cells']
    assert list(p['checklist']['properties']) == ['components'] + YN + ['material_classes']
    assert list(p['checklist']['properties']['components']['items']['properties']) == \
        ['name', 'contact_category', 'duration', 'table_row']
    assert list(p['cells']['items']['properties']) == ['item', 'reason', 'label', 'confidence', 'section', 'needs_sme']
    assert p['checklist']['properties']['components']['items']['properties']['duration']['enum'] == ['A', 'B', 'C', 'none']


def test_good_result_passes(schema):
    assert JO.validate(good(), SHEET, schema) == (True, [])


def mutate(fn):
    r = good()
    fn(r)
    return r


@pytest.mark.parametrize('fn,rules', [
    (lambda r: r.update(device_id='FAKE-2'), ['device_id']),
    (lambda r: r['cells'].pop(), ['items_missing']),
    (lambda r: r['cells'].append(dict(r['cells'][0], item='ISO 10993-99')), ['items_extra']),
    (lambda r: r['cells'].append(copy.deepcopy(r['cells'][0])), ['items_duplicate']),
    (lambda r: r['cells'][0].update(section='4.1'), ['section']),
    (lambda r: r['cells'][0].update(section='§4.'), ['section']),
    (lambda r: r['cells'][0].update(reason=''), ['reason']),
    (lambda r: r['cells'][0].update(reason='two\nlines'), ['reason']),
    (lambda r: r['cells'][0].update(reason='x' * 301), ['reason']),
    (lambda r: r['cells'][0].update(needs_sme='a\rb'), ['needs_sme']),
    (lambda r: r['cells'][0].update(needs_sme='x' * 301), ['needs_sme']),
    (lambda r: r['checklist'].update(components=[]), ['components_empty']),
    (lambda r: r['cells'][0].update(label='maybe_mandatory'), ['schema']),
    (lambda r: r['cells'][0].update(extra='x'), ['schema']),
    (lambda r: r['checklist'].pop('implant'), ['schema']),
])
def test_each_rule(schema, fn, rules):
    ok, failed = JO.validate(mutate(fn), SHEET, schema)
    assert not ok and failed == rules


def test_edge_lengths_pass(schema):
    r = good()
    r['cells'][0].update(reason='x' * 300, needs_sme='y' * 300, section='§10.2.1')
    assert JO.validate(r, SHEET, schema) == (True, [])


@pytest.mark.parametrize('bad', [None, [], 'text', 42, dict(cells='x', checklist=[])])
def test_non_object_results(schema, bad):
    ok, failed = JO.validate(bad, SHEET, schema)
    assert not ok and failed[0] == 'schema' and 'components_empty' in failed


def test_returns_rule_names_only(schema):
    r = good()
    r['cells'][0]['label'] = 'SECRET-LABEL-VALUE'
    r['cells'][1]['reason'] = 'SECRET\nREASON'
    ok, failed = JO.validate(r, SHEET, schema)
    assert set(failed) <= set(JO.RULES)
    assert 'SECRET' not in json.dumps(failed)
