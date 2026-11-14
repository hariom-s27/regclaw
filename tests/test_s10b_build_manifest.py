"""S10b-09 build manifest, on FAKE files only (no real files)."""
import json

import yaml

from eval.s10 import s10b1_manifest as BM


def setup(root, outcome='written'):
    def put(rel, text):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
    put(BM.JUDGE_REL, yaml.safe_dump(dict(entries=[dict(j_id='J-001', items=['a', 'b']),
                                                   dict(j_id='J-002', items=['a'])])))
    put(BM.AUDIT_REL, 'order,j_id,item,family\n1,J-001,a,F1\n2,J-002,a,F1\n')
    put(BM.PRACTICE_REL, yaml.safe_dump(dict(profiles=[{}, {}, {}, {}])))
    put(BM.CODEBOOK_REL, '# fake\nline two\n')
    put(BM.MAP_MANIFEST_REL, json.dumps(dict(files={'jid_map.json': 'f' * 64})))
    put(BM.LEDGER_REL, json.dumps(dict(files_read=[1, 2, 3], outputs_written=[1, 2, 3], outcome=outcome)))


def test_manifest_written_once(tmp_path):
    setup(tmp_path)
    lines = []
    assert BM.run(tmp_path, now='2026-11-14', out=lines.append) == 0
    m = json.loads((tmp_path / BM.MANIFEST_REL).read_text(encoding='utf-8'))
    assert m['seed'] == 'S10b-v1' and m['date'] == '2026-11-14'
    f = m['files']
    assert sorted(f) == sorted(BM.FILES)
    assert f[BM.JUDGE_REL]['entries'] == 2 and f[BM.JUDGE_REL]['items_total'] == 3
    assert f[BM.AUDIT_REL]['rows'] == 2 and f[BM.PRACTICE_REL]['profiles'] == 4
    assert f[BM.CODEBOOK_REL]['lines'] == 2 and f[BM.MAP_MANIFEST_REL]['files_sealed'] == 1
    assert f[BM.LEDGER_REL]['outcome'] == 'written'
    assert all(len(v['sha256_lf']) == 64 for v in f.values())
    before = (tmp_path / BM.MANIFEST_REL).read_bytes()
    assert BM.run(tmp_path, out=lambda s: None) == 3
    assert (tmp_path / BM.MANIFEST_REL).read_bytes() == before


def test_never_reads_map_or_profiles(tmp_path, monkeypatch):
    setup(tmp_path)
    from pathlib import Path
    seen, real = [], Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda self: seen.append(self.name) or real(self))
    BM.run(tmp_path, now='x', out=lambda s: None)
    assert not {'jid_map.json', 'holdout_profiles.yaml', 'dev_profiles.yaml'} & set(seen)


def test_refuses_unless_ledger_written(tmp_path):
    setup(tmp_path, outcome='aborted: duplicate names')
    assert BM.run(tmp_path, out=lambda s: None) == 2
    assert not (tmp_path / BM.MANIFEST_REL).exists()


def test_missing_input(tmp_path):
    setup(tmp_path)
    (tmp_path / BM.AUDIT_REL).unlink()
    assert BM.run(tmp_path, out=lambda s: None) == 2
