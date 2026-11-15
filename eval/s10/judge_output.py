"""S10b2 (S4): validator for one judge's JSON result for one device sheet.

validate(result, sheet) -> (ok, failed rule names). Rules, in this fixed order:
  schema            the result breaks eval/s10/codebook/judge_output.schema.json
  device_id         device_id differs from the sheet id
  items_missing     a sheet item has no cell
  items_extra       a cell names an item that is not on the sheet
  items_duplicate   an item has more than one cell
  section           a section does not match ^§\\d+(\\.\\d+)*$
  reason            a reason is not one line of 1-300 characters
  needs_sme         a needs_sme is not one line of at most 300 characters
  components_empty  checklist.components is missing or empty
Length and pattern rules live here, not in the schema (OpenAI strict structured outputs reject them).
Only rule names are returned: never labels, reasons or jsonschema messages (they can quote values).
"""
import json
import re
from collections import Counter
from pathlib import Path

import jsonschema

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_REL = 'eval/s10/codebook/judge_output.schema.json'
RULES = ('schema', 'device_id', 'items_missing', 'items_extra', 'items_duplicate', 'section', 'reason',
         'needs_sme', 'components_empty')
SECTION_RE = re.compile(r'^§\d+(\.\d+)*$')
TEXT_MAX = 300


def load_schema(root=ROOT):
    return json.loads((Path(root) / SCHEMA_REL).read_text(encoding='utf-8'))


def _one_line(value, min_len, max_len):
    return (isinstance(value, str) and '\n' not in value and '\r' not in value
            and min_len <= len(value.strip() if min_len else value) and len(value) <= max_len)


def validate(result, sheet, schema=None):
    """Returns (ok, [failed rule names in RULES order])."""
    schema = schema if schema is not None else load_schema()
    failed = set()
    if not jsonschema.Draft202012Validator(schema).is_valid(result):
        failed.add('schema')
    res = result if isinstance(result, dict) else {}
    if res.get('device_id') != sheet['id']:
        failed.add('device_id')
    cells = [c for c in (res.get('cells') or []) if isinstance(c, dict)] if isinstance(res.get('cells'), list) else []
    items = Counter(c.get('item') for c in cells)
    want = set(sheet['items'])
    if want - set(items):
        failed.add('items_missing')
    if set(items) - want:
        failed.add('items_extra')
    if any(n > 1 for n in items.values()):
        failed.add('items_duplicate')
    for c in cells:
        if not (isinstance(c.get('section'), str) and SECTION_RE.match(c['section'])):
            failed.add('section')
        if not _one_line(c.get('reason'), 1, TEXT_MAX):
            failed.add('reason')
        if not _one_line(c.get('needs_sme'), 0, TEXT_MAX):
            failed.add('needs_sme')
    checklist = res.get('checklist') if isinstance(res.get('checklist'), dict) else {}
    comps = checklist.get('components')
    if not isinstance(comps, list) or not comps:
        failed.add('components_empty')
    out = [r for r in RULES if r in failed]
    return not out, out
