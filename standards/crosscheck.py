"""Cell-by-cell local PDF-table cross-check through frozen extracted clauses."""
from .data_loader import ENDPOINTS


def crosscheck(clauses, data):
    tables = [c for c in clauses if c['doc'] == 'fda-iso10993-1' and
              c['number'] in {'Attachment A.T1', 'Attachment A.T2'}]
    expected = {(r['nature'], r['site'], r['duration']): r for r in data['matrix']['rows']}
    nature_map = {'Surface device': 'surface', 'External communicating device': 'externally_communicating', 'Implant device': 'implant'}
    site_map = {'Intact skin': 'intact_skin', 'Mucosal membrane': 'mucosal_membrane',
                'Breached or compromised surface': 'breached_compromised_surface',
                'Blood path, indirect': 'blood_path_indirect', 'Tissue+/bone/ dentin': 'tissue_bone_dentin',
                'Circulating blood': 'circulating_blood', 'Tissue+/bone': 'tissue_bone', 'Blood': 'blood'}
    # The extracted cell may wrap internally; accumulate until the next pipe row.
    nature = site = None
    seen, mismatches, symbols = set(), [], {}
    for table in sorted(tables, key=lambda c: c['number']):
        pending = ''
        rows = []
        for line in table['text'].splitlines():
            pending += ' ' + line
            if line.count('|') >= 13:
                rows.append(pending)
                pending = ''
        for line in rows:
            cells = [' '.join(c.split()) for c in line.split('|')]
            positions = [i for i, c in enumerate(cells[:5]) if c in {'A', 'B', 'C'}]
            if not positions:
                continue
            di = positions[0]
            for value in cells[:di]:
                nature = nature_map.get(value, nature)
                site = site_map.get(value, site)
            key = nature, site, cells[di]
            if key not in expected or key in seen:
                raise ValueError(f'Unrecognised or duplicate PDF matrix row: {key}')
            seen.add(key)
            values = cells[di+1:di+12]
            if len(values) != 11:
                raise ValueError(f'Incomplete PDF matrix row: {key}')
            for ep, symbol in zip(ENDPOINTS, values):
                symbols[symbol] = symbols.get(symbol, 0) + 1
                actual = 'X' in symbol or 'O' in symbol
                cell = expected[key]['cells'][ep]
                if actual != cell['consider']:
                    mismatches.append(dict(row=key, endpoint=ep, r9=cell['consider'], pdf=symbol, clause_id=table['id']))
                if ('^' in symbol) != (cell['footnote'] == 'extracorporeal'):
                    mismatches.append(dict(row=key, endpoint=ep, r9_footnote=cell['footnote'], pdf=symbol, clause_id=table['id']))
    missing = sorted(set(expected) - seen)
    if missing:
        raise ValueError(f'PDF cross-check incomplete: missing rows {missing}')
    return dict(rows=len(seen), cells=len(seen)*11, mismatches=mismatches, symbol_counts=symbols,
                source_clauses=[dict(id=c['id'], physical_pdf_page=c['page']) for c in tables],
                verified_by_human=False)
