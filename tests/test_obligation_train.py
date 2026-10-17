import csv
import json
import os
from pathlib import Path
import subprocess
import sys

from obligation.train import L2I, load_data, split


def write_csv(path, field, values):
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow([field])
        writer.writerows([[v] for v in values])


def test_training_excludes_all_leak_sources_and_uses_weak_labels(tmp_path):
    clauses = [dict(id=str(i), text=f'fixture {i}') for i in range(8)]
    weak = [dict(id=str(i), weak_label='SHALL', needs_review=i == 4) for i in range(8)]
    cp, wp = tmp_path / 'clauses.json', tmp_path / 'weak.json'
    cp.write_text(json.dumps(clauses)); wp.write_text(json.dumps(weak))
    for name, field, values in [('train', 'id', map(str, range(7))), ('gold', 'id', ['1']),
                                ('retest', 'id', ['2']), ('dup', 'train_id', ['3'])]:
        write_csv(tmp_path / f'{name}.csv', field, values)
    audit = tmp_path / 'audit.json'
    rows = load_data(cp, wp, tmp_path / 'gold.csv', tmp_path / 'train.csv',
                     tmp_path / 'retest.csv', tmp_path / 'dup.csv', audit)
    assert rows == [('fixture 0', L2I['SHALL']), ('fixture 5', L2I['SHALL']), ('fixture 6', L2I['SHALL'])]
    result = json.loads(audit.read_text())
    assert result['exclusion_counts_sequential'] == dict(outside_train_ids=1, gold=1, retest=1,
                                                        near_duplicate=1, needs_review=1)


def test_validation_is_stratified_and_reproducible():
    rows = [(f'item {i}', i % 4) for i in range(80)]
    train, dev = split(rows, seed=42)
    assert len(train) == 72 and len(dev) == 8
    assert [sum(y == label for _, y in dev) for label in range(4)] == [2] * 4
    assert not {t for t, _ in train} & {t for t, _ in dev}
    assert [train, dev] == split(rows, seed=42)


def test_eight_clause_dry_run_needs_no_torch_or_model(tmp_path):
    clauses = [dict(id=str(i), text=f'fixture words number {i}' + ' extra' * i) for i in range(8)]
    weak = [dict(id=str(i), weak_label=['NONE', 'MAY', 'SHOULD', 'SHALL'][i % 4],
                 needs_review=False) for i in range(8)]
    cp, wp = tmp_path / 'clauses.json', tmp_path / 'weak.json'
    cp.write_text(json.dumps(clauses)); wp.write_text(json.dumps(weak))
    # Fail if the dry-run even attempts to import a heavyweight model dependency.
    runner = """
import sys
class BlockModels:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('torch', 'transformers'):
            raise AssertionError('dry-run attempted a model import')
sys.meta_path.insert(0, BlockModels())
from obligation.train import main
raise SystemExit(main(sys.argv[1:]))
"""
    env = dict(os.environ, HF_HUB_OFFLINE='1')
    run = subprocess.run([sys.executable, '-c', runner, str(cp), str(wp), '--dry-run',
                          '--seed', '42', '--out', str(tmp_path / 'model')],
                         cwd=Path(__file__).resolve().parents[1], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    result = json.loads((tmp_path / 'model/dry_run.json').read_text())
    assert result['fixture_rows'] == 8 and result['optimizer_steps'] == 1
    assert result['model_loaded'] is False
    assert result['loss_after'] < result['loss_before']
