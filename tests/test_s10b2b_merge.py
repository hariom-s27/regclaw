"""S10b2b-05/-06: merge rule and property checks on fake judge results (no real data)."""
import json

import pytest
import yaml

import _netguard
from eval.s10 import merge as MG
from standards.ruleset import normalised_sha


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    _netguard.block_network(monkeypatch, 'Network forbidden')


N, R, M = 'not_applicable', 'recommended', 'mandatory'


def profile(nature='surface', knee=False):
    return dict(device_name='fake', components=[dict(name='c', contact_nature=nature)],
                device_specific_standards=['fda-knee'] if knee else [])


def entry(j_id, items, **kw):
    return dict(j_id=j_id, contact_category='x', duration='A', profile=profile(**kw), items=list(items))


def result(j_id, cells):
    """cells: {item: label or (label, confidence, reason)}."""
    out = []
    for item, v in cells.items():
        label, conf, reason = v if isinstance(v, tuple) else (v, 'high', 'fake reason')
        out.append(dict(item=item, label=label, confidence=conf, reason=reason, section='§1', needs_sme=''))
    return dict(device_id=j_id, checklist={}, cells=out)


def one(labels, item='ISO 10993-5', reasons=('r', 'r', 'r'), **kw):
    """Merge one cell with labels (A, B, C); a None label = that judge has no result."""
    e = entry('J-001', [item], **kw)
    results = {j: {'J-001': (None if lab is None else result('J-001', {item: (lab, 'low', rs)}))}
               for j, lab, rs in zip(MG.JUDGES, labels, reasons)}
    return MG.merge([e], results)['rows'][0]


# ---- median (S10P-04: middle label after sorting NA < rec < mand) --------------------------------------
@pytest.mark.parametrize('labels,final', [
    ((N, N, R), N), ((R, N, N), N), ((N, R, R), R), ((R, R, N), R),
    ((N, N, N), N), ((R, R, R), R), ((M, M, M), M),
])
def test_median_of_three(labels, final):
    row = one(labels)
    assert row['routed'] is False and row['route_reasons'] == [] and row['final'] == final


@pytest.mark.parametrize('labels', [(M, M, R), (M, N, N), (N, R, M), (M, R, N), (R, M, M)])
def test_straddle_routes_including_1_1_1(labels):
    row = one(labels)
    assert row['routed'] is True and row['route_reasons'] == ['straddle'] and row['final'] is None


def test_missing_judge_and_invalid_label_route():
    assert one((M, M, None))['route_reasons'] == ['label_missing_or_invalid']
    assert one((N, 'Mandatory', N))['route_reasons'] == ['label_missing_or_invalid']
    row = one((M, N, None))                         # only 2 valid labels: no straddle reason, still routed
    assert row['route_reasons'] == ['label_missing_or_invalid'] and row['final'] is None
    assert row['labels'] == dict(A=M, B=N, C=None)


def test_judge_result_without_the_item_is_missing():
    e = entry('J-001', ['ISO 10993-5'])
    results = {j: {'J-001': result('J-001', {'ISO 10993-5': N})} for j in MG.JUDGES}
    results['B'] = {'J-001': result('J-001', {'ISO 10993-6': N})}
    assert MG.merge([e], results)['rows'][0]['route_reasons'] == ['label_missing_or_invalid']


# ---- property checks (S10P-08) -------------------------------------------------------------------------
@pytest.mark.parametrize('part', MG.ALWAYS_RULE_PARTS)
def test_always_rule_part_na_on_contacting_device(part):
    row = one((N, N, N), item=part)
    assert row['route_reasons'] == ['always_rule_part_na'] and row['final'] is None
    assert one((R, R, N), item=part)['route_reasons'] == ['always_rule_part_na']
    assert one((R, R, R), item=part)['routed'] is False


def test_always_rule_part_na_not_flagged_on_non_contacting_device():
    row = one((N, N, N), item='ISO 10993-1', nature='non_contact')
    assert row['routed'] is False and row['final'] == N


def test_non_contact_above_na_guard():
    assert one((R, N, N), nature='non_contact')['route_reasons'] == ['non_contact_above_na']
    assert one((N, N, N), nature='non_contact')['routed'] is False
    assert one((R, N, N), nature='surface')['routed'] is False


def test_non_contact_needs_every_component_non_contact():
    p = dict(components=[dict(contact_nature='non_contact'), dict(contact_nature='implant')])
    assert MG.is_contacting(p) is True
    assert MG.is_contacting(dict(components=[dict(contact_nature='non_contact')])) is False


def test_knee_on_non_knee_guard():
    assert one((N, N, N), item='fda-knee')['route_reasons'] == ['knee_on_non_knee']
    assert one((N, N, N), item='fda-knee', knee=True)['routed'] is False


@pytest.mark.parametrize('bad', ['', '   ', None])
def test_empty_reason(bad):
    assert one((R, R, R), reasons=('ok', bad, 'ok'))['route_reasons'] == ['empty_reason']


def test_all_reasons_kept_in_fixed_order():
    row = one((M, N, M), item='ISO 10993-1', reasons=('ok', '', 'ok'))
    assert row['route_reasons'] == ['straddle', 'always_rule_part_na', 'empty_reason']


def test_flags_never_change_labels_and_confidences_kept():
    row = one((N, N, N), item='ISO 10993-12')
    assert row['labels'] == dict(A=N, B=N, C=N)
    assert row['confidences'] == dict(A='low', B='low', C='low')
    assert row['cell_id'] == 'J-001|ISO 10993-12'


# ---- STOP rule -----------------------------------------------------------------------------------------
def straddle_fake(n_routed, n_jury=5):
    items = [f'X-{i}' for i in range(n_routed + n_jury)]
    e = entry('J-001', items)
    labels = {j: {it: (M if (i < n_routed and j == 'A') else R) for i, it in enumerate(items)} for j in MG.JUDGES}
    return [e], {j: {'J-001': result('J-001', labels[j])} for j in MG.JUDGES}


def test_stop_above_200_routed():
    res = MG.merge(*straddle_fake(201))
    assert res['status'] == 'STOP' and res['counts']['routed'] == 201 and res['counts']['jury'] == 5


def test_ok_at_200_routed():
    res = MG.merge(*straddle_fake(200))
    assert res['status'] == 'OK' and res['counts']['routed'] == 200
    assert res['counts']['by_reason']['straddle'] == 200


# ---- CLI on a fake repo --------------------------------------------------------------------------------
def fake_repo(tmp_path, entries, results, *, bad_sha=False):
    jf = tmp_path / MG.JUDGE_FILE_REL
    jf.parent.mkdir(parents=True)
    raw = yaml.safe_dump(dict(format='s10-judge-file.v1', entries=entries), sort_keys=False).encode('utf-8')
    jf.write_bytes(raw)
    man = tmp_path / MG.BUILD_MANIFEST_REL
    man.parent.mkdir(parents=True)
    sha = '0' * 64 if bad_sha else normalised_sha(raw)
    man.write_text(json.dumps(dict(files={MG.JUDGE_FILE_REL: dict(sha256_lf=sha)})), encoding='utf-8')
    for j, by_id in results.items():
        for jid, res in by_id.items():
            if res is not None:
                p = tmp_path / MG.RESULTS_REL / j / f'{jid}.result.json'
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(res), encoding='utf-8')
    return tmp_path


def small_fake():
    entries = [entry('J-001', ['ISO 10993-5', 'ISO 10993-1']), entry('J-002', ['ISO 10993-5'])]
    results = {j: {'J-001': result('J-001', {'ISO 10993-5': N, 'ISO 10993-1': M}),
                   'J-002': result('J-002', {'ISO 10993-5': M if j == 'A' else R})} for j in MG.JUDGES}
    results['C']['J-002'] = None
    return entries, results


def test_cli_writes_once_and_prints_counts_only(tmp_path):
    root = fake_repo(tmp_path, *small_fake())
    lines = []
    assert MG.run(root, now='2026-11-16T00:00:00+00:00', out=lines.append) == 0
    doc = json.loads((root / MG.OUT_REL).read_text(encoding='utf-8'))
    assert doc['counts']['cells'] == 3 and doc['counts']['routed'] == 1 and doc['status'] == 'OK'
    assert doc['counts']['by_reason']['label_missing_or_invalid'] == 1
    assert 'fake reason' not in json.dumps(doc['rows'])                    # reasons are not copied
    assert all('reason' not in row for row in doc['rows'])
    text = '\n'.join(lines)
    for word in (N, R, M, 'J-001', 'J-002', 'fake reason'):
        assert word not in text
    assert 'devices_without_result: A=0 B=0 C=1' in text
    assert lines[-1].startswith(f'{MG.OUT_REL} sha256_lf=')
    assert MG.run(root, out=lines.append) == 3                              # write-once


def test_cli_refuses_on_judge_file_sha_mismatch(tmp_path):
    root = fake_repo(tmp_path, *small_fake(), bad_sha=True)
    lines = []
    assert MG.run(root, out=lines.append) == 2
    assert not (root / MG.OUT_REL).exists()


def test_cli_stop_writes_nothing(tmp_path):
    root = fake_repo(tmp_path, *straddle_fake(201))
    lines = []
    assert MG.run(root, out=lines.append) == 4
    assert not (root / MG.OUT_REL).exists()
    assert lines[-1].startswith('STOP: routed 201 > 200')
