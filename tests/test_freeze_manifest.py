"""Final-holdout freeze check on synthetic files only; never touches real data."""
import csv
import hashlib
import json
from pathlib import Path

import pytest

from goldset.score_entities import HASH_METHOD, SENSITIVITY_LABEL, s4b_main, sha256_file, verify_manifest
from ner.normalise import NORMALISER_VERSION

TYPES = ('biological_effects', 'chemicals', 'device_types', 'regulatory_actors', 'lifecycle_phases')
HOLDOUT = [f'x:h{i}' for i in range(20)]
DEV = ['x:d0', 'x:d1']
EXPOSED = 'x:h0'
TEXT = 'The heart valve and the guidewire are tested.'


def _write(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(value if isinstance(value, str) else json.dumps(value), encoding='utf-8')


def _entities(canonicals):
    return {k: [{'canonical': c} for c in canonicals] if k == 'device_types' else [] for k in TYPES}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ids = DEV + HOLDOUT
    with open('ids.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.writer(f)
        w.writerow(['clause_id', 'split'])
        w.writerows([(c, 'dev') for c in DEV] + [(c, 'holdout') for c in HOLDOUT])
    with open('gold.csv', 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, ['clause_id', 'source', 'text', *TYPES, 'notes'])
        w.writeheader()
        for c in ids:
            w.writerow(dict(clause_id=c, source='x', text=TEXT, device_types='heart valve => heart valve',
                            notes='complete', **{k: '' for k in TYPES if k != 'device_types'}))
    _write('clauses.json', [dict(id=c, text=TEXT) for c in ids])
    sha = hashlib.sha256(TEXT.encode()).hexdigest()
    # The exposed clause has a wrong prediction, so 20- and 19-clause scores differ.
    pred = lambda c: ['guidewire'] if c == EXPOSED else ['heart valve']
    for run in ('low', 'high'):
        _write(f'{run}/ner.json', [dict(id=c, entities=_entities(pred(c))) for c in ids])
        _write(f'{run}/cache.json', {f'{c}|{sha}|p|azure_openai|gpt-5-mini|{run}': {'data': {'entities': _entities(pred(c))}}
                                     for c in ids})
        _write(f'{run}/ledger.json', [dict(clause_id=c, effort=run, cost_estimate_usd=.01, latency_s=1.) for c in ids])
    frozen = ['gold.csv', 'ids.csv', 'clauses.json', 'low/ner.json', 'low/cache.json', 'high/ner.json', 'high/cache.json']
    manifest = dict(normaliser_version=NORMALISER_VERSION, holdout_clauses=20, sensitivity_exclusion=[EXPOSED],
                    hash_method=HASH_METHOD, files={p: sha256_file(p) for p in frozen})
    _write('manifest.json', manifest)
    return tmp_path


def _args(output='out.json', *extra):
    return ['--completed-hand-gold', '--final-holdout', '--manifest', 'manifest.json', '--gold', 'gold.csv',
            '--ids', 'ids.csv', '--clauses', 'clauses.json', '--low', 'low/ner.json', 'low/cache.json', 'low/ledger.json',
            '--high', 'high/ner.json', 'high/cache.json', 'high/ledger.json', '--output', output, *extra]


def test_matching_manifest_scores_20_plus_19_sensitivity(tree):
    assert s4b_main(_args()) == 0
    out = json.loads(Path('out.json').read_text())
    assert out['split'] == 'holdout' and out['clauses'] == 20 and out['manifest']['verified']
    assert 'errors_low' not in out
    sens = out['sensitivity']
    assert sens['label'] == SENSITIVITY_LABEL and sens['excluded'] == [EXPOSED] and sens['clauses'] == 19
    f1 = lambda block: block['low_after']['metrics']['canonical']['micro']['f1']
    assert f1(sens['scores']) == 1.0 and f1(out['scores']) < 1.0
    assert 'ci95' in sens['scores']['low_after']['metrics']['canonical']['device_types']
    assert set(sens['diff_high_minus_low']) == {'after', 'before'}


def test_one_changed_file_aborts_before_scoring(tree):
    Path('low/ner.json').write_text(Path('low/ner.json').read_text() + ' ', encoding='utf-8')
    with pytest.raises(ValueError, match=r'low/ner.json: sha256 differs'):
        s4b_main(_args())
    assert not Path('out.json').exists()


def test_version_mismatch_and_missing_file_abort(tree):
    manifest = json.loads(Path('manifest.json').read_text())
    _write('manifest.json', dict(manifest, normaliser_version='1.0'))
    with pytest.raises(ValueError, match='normaliser version'):
        s4b_main(_args())
    _write('manifest.json', manifest)
    with pytest.raises(ValueError, match='normaliser version'):
        s4b_main(_args('out.json', '--normaliser-version', '1.0'))
    Path('high/cache.json').unlink()
    with pytest.raises(ValueError, match='high/cache.json: missing'):
        s4b_main(_args())


def test_unlisted_input_and_existing_output_abort(tree):
    Path('other.csv').write_bytes(Path('gold.csv').read_bytes())
    with pytest.raises(ValueError, match='other.csv: not in the manifest'):
        s4b_main(_args('out.json', '--gold', 'other.csv'))
    Path('out.json').write_text('{}')
    with pytest.raises(SystemExit):
        s4b_main(_args())
    assert verify_manifest('manifest.json')['holdout_clauses'] == 20


def test_dev_mode_unchanged_and_ignores_manifest(tree):
    args = [a for a in _args('dev.json') if a != '--final-holdout']
    Path('low/ner.json').write_text(Path('low/ner.json').read_text() + ' ', encoding='utf-8')
    assert s4b_main(args) == 0
    out = json.loads(Path('dev.json').read_text())
    assert out['split'] == 'dev' and out['clauses'] == 2 and 'sensitivity' not in out and 'errors_low' in out


def test_tracked_files_hash_line_ending_neutral_data_files_raw(tree):
    lf, crlf = b'x = 1\ny = 2\n', b'x = 1\r\ny = 2\r\n'
    Path('code.py').write_bytes(lf)
    Path('data').mkdir()
    Path('data/raw.json').write_bytes(b'{\n}\n')
    manifest = json.loads(Path('manifest.json').read_text())
    manifest['files'].update({'code.py': sha256_file('code.py'), 'data/raw.json': sha256_file('data/raw.json')})
    _write('manifest.json', manifest)
    assert sha256_file('code.py') == hashlib.sha256(lf).hexdigest()
    Path('code.py').write_bytes(crlf)  # CRLF copy of the same tracked file
    verify_manifest('manifest.json')
    Path('data/raw.json').write_bytes(b'{\r\n}\r\n')  # data/ stays byte-exact
    with pytest.raises(ValueError, match='data/raw.json: sha256 differs'):
        verify_manifest('manifest.json')
    _write('manifest.json', dict(manifest, hash_method='raw'))
    with pytest.raises(ValueError, match='hash method'):
        verify_manifest('manifest.json')
