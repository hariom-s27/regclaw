"""Strict clause/NER/obligation join into the frozen enriched contract."""
import argparse
from collections import Counter
import json
import hashlib
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
    parser.add_argument('--ner', type=Path, default=ROOT / 'data/ner.json')
    parser.add_argument('--output', type=Path, default=ROOT / 'data/enriched.json')
    args = parser.parse_args(argv)
    path = {'weak': ROOT / 'data/weak_labels.json', 'legalbert': ROOT / 'data/obligation_predictions.json'}.get(
        args.obligation_source, Path(args.obligation_source))
    read = lambda p: json.loads(Path(p).read_text(encoding='utf-8'))
    ner_rows = read(args.ner)
    rows = merge(read(ROOT / 'data/clauses.json'), ner_rows, read(path), args.allow_missing_ner)
    from ner.validate_enriched import validate
    validate(rows, read(ROOT / 'data/clauses.json'))
    metadata = [n['provenance'] for n in ner_rows if n.get('provenance')]
    provisional = metadata and any(m.get('provisional') for m in metadata)
    if provisional:
        providers = {m['provider'] for m in metadata}
        if len(providers) != 1:
            raise ValueError('Do not mix providers in a provisional enriched file')
        separated = (ROOT / 'data/runs' / next(iter(providers))).resolve()
        if not args.output.resolve().is_relative_to(separated):
            raise ValueError('Provisional enriched output must stay in the provider-separated run directory')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    if provisional:
        manifest = dict(provenance=metadata[0], model_versions=sorted({m['model_version'] for m in metadata if m.get('model_version')}),
                        ner_source=str(args.ner), obligation_source=str(path),
                        input_sha256=hashlib.sha256(args.ner.read_bytes()).hexdigest(),
                        output_sha256=hashlib.sha256(args.output.read_bytes()).hexdigest())
        args.output.with_suffix('.provenance.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print(f'enriched={len(rows)} obligation={dict(Counter(r["obligation"] for r in rows))}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
