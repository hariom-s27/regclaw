"""S10b-02 grid check, on FAKE grids and a FAKE ruleset only (no real files)."""
import itertools
import json

import yaml

from eval.s10 import e1_grid_check as G

NATURES = ('surface', 'implant', 'externally_communicating')
SITES = ('intact_skin', 'mucosal_membrane', 'blood_path_indirect', 'tissue_bone')
DURS = ('A', 'B')  # 3 x 4 x 2 = 24 fake rows


def fake_keys():
    return list(itertools.product(NATURES, SITES, DURS))


def fake_consider(i, col):
    return (i + G.ENDPOINTS.index(col)) % 3 == 0


def write_fake(root, flip=None, bad_mark=False, drop_row=False, crlf=True):
    keys = fake_keys()
    rows = []
    for i, (n, s, d) in enumerate(keys):
        cells = {c: dict(consider=fake_consider(i, c), footnote='extracorporeal' if i == 5 and c == 'Hemo' else None)
                 for c in G.ENDPOINTS}
        rows.append(dict(nature=n, site=s, duration=d, cells=cells))
    ypath = root / G.YAML_REL
    ypath.parent.mkdir(parents=True, exist_ok=True)
    ypath.write_text(yaml.safe_dump(dict(version=1, rows=rows), sort_keys=False), encoding='utf-8')
    lines = [','.join(G.HEADER)]
    for i, key in enumerate(keys):
        if drop_row and i == 0:
            continue
        marks = []
        for c in G.ENDPOINTS:
            m = fake_consider(i, c)
            if flip == (i, c):
                m = not m
            marks.append(('X' if i % 2 else 'O') if m else '')
        if bad_mark and i == 3:
            marks[0] = 'Y'
        info = ['X' if i == 7 else '', '']
        foot = 'a' if i == 5 else ''
        lines.append(','.join(list(key) + marks + info + [foot]))
    gpath = root / G.GRID_REL
    gpath.parent.mkdir(parents=True, exist_ok=True)
    gpath.write_bytes(('\r\n' if crlf else '\n').join(lines).encode('utf-8'))


def manifest(root):
    return json.loads((root / G.MANIFEST_REL).read_text(encoding='utf-8'))


def test_matching_grid(tmp_path):
    write_fake(tmp_path)
    assert G.main(tmp_path, now='2026-11-14T00:00:00Z') == 0
    m = manifest(tmp_path)
    assert (m['rows'], m['cells_compared'], m['mismatches'], m['verdict']) == (24, 264, [], 'match')
    assert m['repro_degr_info']['Repro']['marked_count'] == 1
    assert m['repro_degr_info']['Degr']['marked_count'] == 0
    assert len(m['footnote_notes']) == 1
    assert m['footnote_notes'][0]['yaml_cell_footnotes'] == {'Hemo': 'extracorporeal'}
    assert len(m['grid_sha256_lf']) == 64 and len(m['yaml_sha256_lf']) == 64
    assert m['yaml_file'] == G.YAML_REL


def test_lf_sha_ignores_line_endings(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    write_fake(a, crlf=True)
    write_fake(b, crlf=False)
    G.main(a, now='x'), G.main(b, now='x')
    assert manifest(a)['grid_sha256_lf'] == manifest(b)['grid_sha256_lf']


def test_mismatch_is_listed_and_exit_1(tmp_path):
    write_fake(tmp_path, flip=(4, 'Geno'))
    assert G.main(tmp_path, now='x') == 1
    m = manifest(tmp_path)
    n, s, d = fake_keys()[4]
    assert m['verdict'] == 'mismatch'
    assert m['mismatches'] == [dict(nature=n, site=s, duration=d, column='Geno')]
    written = [p for p in tmp_path.rglob('*') if p.is_file()]
    assert sorted(p.name for p in written) == sorted(['e1_grid_check.json', 'e1_fda_grid_hariom.csv',
                                                      'fda2023_attachment_a.v1.yaml'])


def test_write_once_refused(tmp_path):
    write_fake(tmp_path)
    assert G.main(tmp_path, now='x') == 0
    before = (tmp_path / G.MANIFEST_REL).read_bytes()
    assert G.main(tmp_path, now='y') == 3
    assert (tmp_path / G.MANIFEST_REL).read_bytes() == before


def test_bad_mark_is_input_error(tmp_path):
    write_fake(tmp_path, bad_mark=True)
    assert G.main(tmp_path) == 2
    assert not (tmp_path / G.MANIFEST_REL).exists()


def test_missing_row_is_input_error(tmp_path):
    write_fake(tmp_path, drop_row=True)
    assert G.main(tmp_path) == 2
    assert not (tmp_path / G.MANIFEST_REL).exists()
