"""Strict clause/NER/obligation join into the frozen enriched contract."""
import argparse
from collections import Counter
import json
from pathlib import Path

from ner.normalise import Normaliser
from ner.stdref import references

ROOT = Path(__file__).resolve().parents[1]
CLAUSE_FIELDS = {'id', 'doc', 'number', 'title', 'text', 'parent', 'source_pdf', 'page', 'informative'}
ENTITY_FIELDS = ('biological_effects', 'chemicals', 'device_types', 'regulatory_actors', 'lifecycle_phases', 'keywords')
LABELS = ('SHALL', 'SHOULD', 'MAY', 'NONE')
ENRICHED_FIELDS = CLAUSE_FIELDS | {'obligation', 'obligation_flags', 'entities', 'iso_refs'}


def indexed(rows, name):
    result = {}
    for row in rows:
        cid = row['id']
        if cid in result:
            raise ValueError(f'{name}: duplicate id {cid}')
        result[cid] = row
    return result


def merge(clauses, ner, predictions, allow_missing_ner=False):
    cmap, nmap, pmap = (indexed(rows, name) for rows, name in
                        [(clauses, 'clauses'), (ner, 'ner'), (predictions, 'predictions')])
    for name, mapping in [('ner', nmap), ('predictions', pmap)]:
        unknown = mapping.keys() - cmap.keys()
        if unknown:
            raise ValueError(f'{name}: unknown ids {sorted(unknown)}')
    missing = cmap.keys() - nmap.keys()
    if missing and not allow_missing_ner:
        raise ValueError(f'ner: missing {len(missing)} clauses; use --allow-missing-ner for a draft')
    if missing:
        print(f'WARNING: {len(missing)} clauses missing NER; using empty entities')
    normaliser, output = Normaliser(), []
    for c in clauses:
        if set(c) != CLAUSE_FIELDS:
            raise ValueError(f"{c['id']}: clause fields differ from the contract")
        n, p = nmap.get(c['id'], {}), pmap.get(c['id'], {})
        label = p.get('label', p.get('weak_label', p.get('obligation', 'NONE')))
        if label not in LABELS:
            raise ValueError(f"{c['id']}: invalid obligation {label!r}")
        entities = {}
        for kind in ENTITY_FIELDS:
            values = n.get('entities', {}).get(kind, n.get(kind, []))
            names = []
            for value in values:
                if isinstance(value, str):
                    mention, canonical = value, value
                else:
                    mention, canonical = value['mention'], value['canonical']
                name, _ = normaliser.canonical(mention, kind, c['text'], enum_canonical=canonical)
                if name:
                    names.append(name)
            entities[kind] = sorted(set(names))
        refs = set()
        for ref in n.get('iso_refs', []):
            ids, _ = references(ref)
            if len(ids) != 1:
                raise ValueError(f"{c['id']}: invalid standard reference {ref!r}")
            refs.update(ids)
        flags = p.get('flags', p.get('obligation_flags', []))
        if not isinstance(flags, list) or not all(isinstance(f, str) for f in flags):
            raise ValueError('obligation flags must be strings')
        output.append({**c, 'obligation': label, 'obligation_flags': sorted(set(flags)),
                       'entities': entities, 'iso_refs': sorted(refs)})
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--obligation-source', default='weak')
    parser.add_argument('--allow-missing-ner', action='store_true')
    args = parser.parse_args(argv)
    path = {'weak': ROOT / 'data/weak_labels.json', 'legalbert': ROOT / 'data/obligation_predictions.json'}.get(
        args.obligation_source, Path(args.obligation_source))
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    rows = merge(read(ROOT / 'data/clauses.json'), read(ROOT / 'data/ner.json'), read(path), args.allow_missing_ner)
    from ner.validate_enriched import validate
    validate(rows, read(ROOT / 'data/clauses.json'))
    (ROOT / 'data/enriched.json').write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'enriched={len(rows)} obligation={dict(Counter(r["obligation"] for r in rows))}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
