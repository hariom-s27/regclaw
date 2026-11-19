"""S10c dev export on an invented repository built entirely inside tmp_path."""
import builtins
import copy
import csv
import io
import json
import os
from pathlib import Path

import pytest
import yaml

import _netguard
from eval.s10 import dev_export as E
from eval.s10 import addendum_freeze as AF
from eval.s10 import formats as F
from eval.s10 import judge_file as JF
from eval.s10 import label_quality as Q
from eval.s10 import second_rater as SR
from eval.s10 import profile_checks as PC
from standards.ruleset import normalised_sha

M, N, R = 'mandatory', 'not_applicable', 'recommended'
PARTS = [f'ISO 10993-{i}' for i in range(1, 21)]
STAMP = '2026-11-19T00:00:00+00:00'


@pytest.fixture(autouse=True)
def fake_only(monkeypatch, tmp_path):
    monkeypatch.setenv('REGCLAW_STUB', '1')
    monkeypatch.setenv('LANGFUSE_ENABLED', 'false')
    _netguard.block_network(monkeypatch, 'dev export network forbidden')
    repo = Path(__file__).resolve().parents[1]
    attempts = []

    def check(file, mode='r'):
        if not isinstance(file, (str, os.PathLike)):
            return
        path = Path(file).resolve()
        if any(part.casefold() in E.FORBIDDEN_FILE_NAMES for part in path.parts):
            attempts.append(path.name)
            raise AssertionError('forbidden file access')
        if not path.is_relative_to(tmp_path.resolve()) and path.is_relative_to(repo / 'data'):
            raise AssertionError('real data access')
        if any(c in mode for c in 'wax+') and not path.is_relative_to(tmp_path.resolve()):
            raise AssertionError('write outside fake temp root')

    def guarded(real):
        def opened(file, mode='r', *args, **kwargs):
            check(file, mode)
            return real(file, mode, *args, **kwargs)
        return opened

    original_read_bytes = Path.read_bytes

    def read_bytes(path):
        check(path)
        return original_read_bytes(path)

    monkeypatch.setattr(builtins, 'open', guarded(builtins.open))
    monkeypatch.setattr(io, 'open', guarded(io.open))
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    return attempts


def put(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(text)
    return path


def json_text(doc):
    return json.dumps(doc, indent=2, sort_keys=True) + '\n'


def csv_text(fields, rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def profile_row(index):
    dev = index < 10
    return dict(id=f'P-FAKE-{index + 1:02d}', stratum=f'S{index + 1}' if index < 8 else 'trap',
                split='dev' if dev else 'holdout', contact_category='surface/intact_skin', duration='A', flags=[],
                profile=dict(device_name=f'FAKE-DEV-{index}' if dev else f'PRIVATE-UNMATCHED-{index}',
                             device_specific_standards=['fda-knee'] if index in (0, 10) else [],
                             components=[dict(name='FAKE component', contact_nature='surface',
                                              contact_site='intact_skin', exposure=dict(pattern='single', hours_per_use=2.0),
                                              materials=[dict(name='FAKE material', material_class='other')],
                                              sterilization='non_sterile', existing_evidence=dict(
                                                  equivalent_marketed_device=True))]))


def make_world():
    profiles = [profile_row(i) for i in range(30)]
    entries = [JF.judge_entry(f'J-{30 - i:03d}', row, PARTS) for i, row in enumerate(profiles)]
    pairs = [(entry['j_id'], item) for entry in entries for item in entry['items']]
    ids = [E.cell_id(*pair) for pair in pairs]
    mandatory = set(ids[::2][:298])
    remaining = [cid for cid in ids if cid not in mandatory]
    recommended = set(remaining[:16])
    label_of = {cid: M if cid in mandatory else R if cid in recommended else N for cid in ids}
    groups = {label: [cid for cid in ids if label_of[cid] == label] for label in (M, N, R)}
    prefix = groups[M][:38] + groups[M][-38:] + groups[N][:34] + groups[N][-33:] + groups[R][:7]
    audit_ids = prefix + [cid for cid in ids if cid not in set(prefix)][:200]
    audit = [dict(order=str(i), j_id=cid.split('|', 1)[0], item=cid.split('|', 1)[1], family='FAKE')
             for i, cid in enumerate(audit_ids, 1)]
    merged = [dict(cell_id=cid, j_id=jid, item=item, labels=dict.fromkeys('ABC', label_of[cid]),
                   confidences=dict.fromkeys('ABC', 'high'), final=label_of[cid], routed=False, route_reasons=[])
              for (jid, item), cid in zip(pairs, ids)]
    by_id = {row['cell_id']: row for row in merged}
    split_ids = [cid for cid in prefix if label_of[cid] == N][:2]
    split_ids += [cid for cid in ids if label_of[cid] == N and cid not in set(prefix)][:1]
    for cid in split_ids:
        by_id[cid]['labels']['C'] = R
    return dict(profiles=profiles[:10], entries=entries, merged=merged, audit=audit, split_ids=split_ids)


def write_world(root, *, mutate=None, bad_sha=None, crlf=False, malformed=None):
    world = make_world()
    if mutate:
        mutate(world)
    dev_doc = dict(format=F.PROFILES_FORMAT, split='dev', profiles=world['profiles'])
    judge_doc = dict(format=JF.JUDGE_FORMAT, entries=world['entries'])
    texts = {JF.DEV_REL: yaml.safe_dump(dev_doc, sort_keys=False),
             JF.JUDGE_REL: yaml.safe_dump(judge_doc, sort_keys=False),
             JF.PARTS_REL: yaml.safe_dump(dict(parts=[dict(part=part) for part in PARTS])),
             E.D.LIST_REL: csv_text(('order', 'j_id', 'item', 'family'), world['audit']),
             SR.SHEET_REL: 'FAKE opaque sheet; hashed only.\n'}
    judge_sha = normalised_sha(texts[JF.JUDGE_REL].encode('utf-8'))
    merge_doc = dict(format='s10-merge.v1', status='OK', judge_file_sha256_lf=judge_sha, rows=world['merged'])
    texts[E.D.MERGE_REL] = json_text(merge_doc)
    # Build the valid friend draw before any negative-case corruption of audit labels.
    draw_rows = copy.deepcopy(world['merged'])
    for row in draw_rows:
        for judge in E.JUDGES:
            if row['labels'].get(judge) not in E.LABELS:
                row['labels'][judge] = row['final']
    draw = SR.friend_draw(draw_rows, world['audit'])
    merged = {row['cell_id']: row for row in draw_rows}
    error_id = next(cell['cell_id'] for cell in draw['cells'] if cell['stratum'] == 'unaudited_mandatory')
    friend_rows = [dict(sheet_id=cell['sheet_id'], label=R if cell['cell_id'] == error_id else merged[cell['cell_id']]['final'],
                        unsure_guideline_gap='', note='') for cell in draw['cells']]
    texts[SR.DRAW_REL] = json_text(draw)
    texts[SR.LABELS_REL] = csv_text(SR.FIELDS, friend_rows)
    human_rows = [dict(order=cell['order'], j_id=cell['j_id'], item=cell['item'],
                       label=merged[E.cell_id(cell['j_id'], cell['item'])]['final'], confidence='high',
                       reason='FAKE human rationale', timestamp=STAMP) for cell in world['audit'][:E.LF.UPTO]]
    texts[E.D.HUMAN_REL] = csv_text(E.LF.FIELDS, human_rows)
    if malformed:
        texts[malformed] = 'profiles: [PRIVATE-PARSER-MARKER\n'
    sha = lambda rel: normalised_sha(texts[rel].encode('utf-8'))
    texts[E.PROFILES_MANIFEST_REL] = json_text(dict(format='s10-profiles-manifest.v1', files={
        Path(JF.DEV_REL).name: dict(sha256_lf=sha(JF.DEV_REL))}))
    texts[E.BUILD_REL] = json_text(dict(format='s10b1-build-manifest.v1', files={
        JF.JUDGE_REL: dict(sha256_lf=sha(JF.JUDGE_REL)), E.D.LIST_REL: dict(sha256_lf=sha(E.D.LIST_REL))}))
    texts[E.Q.HUMAN_MANIFEST_REL] = json_text(dict(format=E.LF.FORMAT, labeller='hariom', file=E.D.HUMAN_REL,
                                                 upto=E.LF.UPTO, data_rows=E.LF.UPTO,
                                                 sha256_lf=sha(E.D.HUMAN_REL),
                                                 audit_list_sha256_lf=sha(E.D.LIST_REL)))
    texts[SR.MANIFEST_REL] = json_text(dict(format='s10-friend-labels-manifest.v1', file=SR.LABELS_REL,
                                          rows=len(draw['cells']), valid_rows=len(draw['cells']),
                                          sha256_lf=sha(SR.LABELS_REL), friend_set_sha256_lf=sha(SR.DRAW_REL),
                                          sheet_sha256_lf=sha(SR.SHEET_REL)))
    if bad_sha:
        manifest_rel, keys = {
            JF.DEV_REL: (E.PROFILES_MANIFEST_REL, ('files', Path(JF.DEV_REL).name, 'sha256_lf')),
            JF.JUDGE_REL: (E.BUILD_REL, ('files', JF.JUDGE_REL, 'sha256_lf')),
            E.D.HUMAN_REL: (E.Q.HUMAN_MANIFEST_REL, ('sha256_lf',)),
            SR.LABELS_REL: (SR.MANIFEST_REL, ('sha256_lf',)),
            SR.DRAW_REL: (SR.MANIFEST_REL, ('friend_set_sha256_lf',)),
            SR.SHEET_REL: (SR.MANIFEST_REL, ('sheet_sha256_lf',)),
            E.D.LIST_REL: (E.BUILD_REL, ('files', E.D.LIST_REL, 'sha256_lf')),
        }[bad_sha]
        doc = json.loads(texts[manifest_rel])
        target = doc
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = '0' * 64
        texts[manifest_rel] = json_text(doc)
    for rel, text in texts.items():
        put(root, rel, text.replace('\n', '\r\n') if crlf else text)
    world['draw'] = draw
    return world


def exported(root):
    return [root / rel for rel in E.OUTPUTS if (root / rel).exists()]


def go(root):
    lines = []
    code = E.main(['run'], root=root, out=lines.append)
    return code, lines


def test_export_matches_ten_dev_and_twenty_unmatched(tmp_path):
    root = tmp_path / 'world'
    world = write_world(root)
    code, lines = go(root)
    assert code == 0 and len(exported(root)) == len(E.OUTPUTS)
    summary = json.loads((root / E.SUMMARY_REL).read_bytes())
    assert summary['counts']['dev_profiles'] == summary['counts']['matched_entries'] == 10
    assert summary['counts']['unmatched_entries'] == 20
    assert summary['counts']['dev_cells'] == sum(len(entry['items']) for entry in world['entries'][:10])
    assert summary['e1_input']['population'] == 298 and summary['e2_pool'] == dict(x=1, n=126)
    assert format(summary['e1_input']['weighted_binary_rate_on_final_mandatory'], '.4f') == '0.0149'
    assert any('E1_match=1' in line and 'E2_match=1' in line for line in lines)


def test_matching_uses_canonical_content_and_stripped_evidence(tmp_path):
    def mutate(world):
        entry = world['entries'][0]
        entry['profile'] = dict(reversed(list(entry['profile'].items())))
        world['profiles'][0]['profile']['components'][0]['existing_evidence']['equivalent_marketed_device'] = False
    write_world(tmp_path, mutate=mutate)
    assert go(tmp_path)[0] == 0


@pytest.mark.parametrize('kind', ['zero', 'multiple', 'reused', 'duplicate-dev', 'duplicate-judge', 'unmatched'])
def test_match_refusals_write_nothing(tmp_path, kind):
    def mutate(world):
        if kind == 'zero':
            world['entries'][0]['profile']['device_name'] = 'PRIVATE-NO-MATCH'
        elif kind == 'multiple':
            world['entries'][10]['profile'] = copy.deepcopy(world['entries'][0]['profile'])
        elif kind == 'reused':
            world['profiles'][1]['profile'] = copy.deepcopy(world['profiles'][0]['profile'])
        elif kind == 'duplicate-dev':
            world['profiles'][1]['id'] = world['profiles'][0]['id']
        elif kind == 'duplicate-judge':
            world['entries'][1]['j_id'] = world['entries'][0]['j_id']
        else:
            world['entries'].append(dict(j_id='J-EXTRA', contact_category='surface/intact_skin', duration='A',
                                         profile=dict(device_name='PRIVATE-EXTRA'), items=PARTS))
    write_world(tmp_path, mutate=mutate)
    code, lines = go(tmp_path)
    assert code == 1 and exported(tmp_path) == []
    assert 'PRIVATE' not in '\n'.join(lines) and 'J-EXTRA' not in '\n'.join(lines)


@pytest.mark.parametrize('kind', ['missing', 'extra', 'knee'])
def test_items_mismatch_refuses(tmp_path, kind):
    def mutate(world):
        if kind == 'missing':
            world['entries'][0]['items'] = world['entries'][0]['items'][1:]
        elif kind == 'extra':
            world['entries'][0]['items'].append('PRIVATE-EXTRA-ITEM')
        else:
            world['profiles'][0]['profile']['device_specific_standards'] = []
            world['entries'][0]['profile']['device_specific_standards'] = []
    write_world(tmp_path, mutate=mutate)
    assert go(tmp_path)[0] == 1 and exported(tmp_path) == []


def test_forbidden_files_are_never_opened(tmp_path, fake_only):
    write_world(tmp_path)
    assert go(tmp_path)[0] == 0 and fake_only == []
    assert go(tmp_path)[0] == 3 and fake_only == []
    for name in E.FORBIDDEN_FILE_NAMES:
        with pytest.raises(E.ExportError, match='forbidden_path'):
            E.fixed_path(tmp_path, name)
    assert fake_only == []


@pytest.mark.parametrize('rel', [JF.DEV_REL, JF.JUDGE_REL, E.D.HUMAN_REL, SR.LABELS_REL,
                                 SR.DRAW_REL, SR.SHEET_REL, E.D.LIST_REL])
def test_each_input_sha_mismatch_refuses(tmp_path, rel, fake_only):
    write_world(tmp_path, bad_sha=rel)
    assert go(tmp_path)[0] in (1, 2) and exported(tmp_path) == [] and fake_only == []


def test_crlf_inputs_use_lf_hashes(tmp_path):
    write_world(tmp_path, crlf=True)
    assert go(tmp_path)[0] == 0
    for rel in E.OUTPUTS:
        assert b'\r' not in (tmp_path / rel).read_bytes()


@pytest.mark.parametrize('rel', E.OUTPUTS)
def test_existing_output_precedes_input_reads(tmp_path, monkeypatch, rel):
    path = put(tmp_path, rel, 'FAKE existing\n')
    before = path.read_bytes()
    def fail(*args, **kwargs):
        raise AssertionError('input read before existing-output refusal')
    monkeypatch.setattr(E, 'load_inputs', fail)
    assert go(tmp_path) == (3, ['output_exists count=1'])
    assert path.read_bytes() == before and len(exported(tmp_path)) == 1


def test_exclusive_create_preserves_existing_bytes(tmp_path, monkeypatch):
    write_world(tmp_path)
    original = E.write_once
    def race(root, rel, text):
        if rel == E.GOLD_REL:
            put(root, rel, 'FAKE competing output\n')
        return original(root, rel, text)
    monkeypatch.setattr(E, 'write_once', race)
    assert go(tmp_path)[0] == 3
    assert (tmp_path / E.CELLS_REL).is_file()
    assert (tmp_path / E.GOLD_REL).read_text(encoding='utf-8') == 'FAKE competing output\n'
    assert not (tmp_path / E.SUMMARY_REL).exists() and not (tmp_path / E.MANIFEST_REL).exists()


def test_exported_gold_v2_validates_and_hashes_dev_cells(tmp_path, monkeypatch):
    world = write_world(tmp_path)
    order = []
    original = E.write_once
    def capture(root, rel, text):
        order.append(rel)
        if rel == E.GOLD_REL:
            assert (root / E.CELLS_REL).is_file()
        return original(root, rel, text)
    monkeypatch.setattr(E, 'write_once', capture)
    assert go(tmp_path)[0] == 0 and tuple(order) == E.OUTPUTS
    doc = yaml.safe_load((tmp_path / E.GOLD_REL).read_text(encoding='utf-8'))
    gold = F.validate_gold(doc, {row['id']: row for row in world['profiles']},
                           {'part_map': {'parts': [{'part': part} for part in PARTS]}})
    assert len(gold) == 201
    assert doc['reference']['export_sha256_lf'] == normalised_sha((tmp_path / E.CELLS_REL).read_bytes())
    assert doc['reference']['merge_sha256_lf'] == normalised_sha((tmp_path / E.D.MERGE_REL).read_bytes())


def test_dev_cells_have_exact_fields_and_nullable_humans(tmp_path):
    world = write_world(tmp_path)
    inputs = E.load_inputs(tmp_path)
    assert go(tmp_path)[0] == 0
    cells = json.loads((tmp_path / E.CELLS_REL).read_bytes())
    expected = {'profile_id', 'stratum', 'item', 'final', 'routed', 'split_cell', 'labels', 'human', 'friend'}
    matched, _ = E.match_dev_entries(world['profiles'], world['entries'], PARTS)
    by_id = {row['cell_id']: row for row in world['merged']}
    for cell in cells:
        assert set(cell) == expected
        cid = E.cell_id(matched[cell['profile_id']]['j_id'], cell['item'])
        assert cell['final'] == by_id[cid]['final'] and cell['labels'] == by_id[cid]['labels']
        assert cell['split_cell'] == (len(set(cell['labels'].values())) > 1)
        assert cell['human'] == inputs['hariom'].get(cid)
        assert cell['friend'] == (inputs['friend'][cid]['label'] if cid in inputs['friend'] else None)
    assert any(cell['human'] is None for cell in cells) and any(cell['friend'] is None for cell in cells)
    assert any(cell['split_cell'] for cell in cells)


def test_binary_gaps_use_recorded_split_labels():
    rows = [dict(cell_id='FAKE|a', final=M, labels=dict(A=M, B=R, C=N)),
            dict(cell_id='FAKE|b', final=N, labels=dict(A=M, B=R, C=N)),
            dict(cell_id='FAKE|c', final=R, labels=dict(A=R, B=N, C=M))]
    result = E.audit_gaps(rows, {'FAKE|a': M, 'FAKE|b': N, 'FAKE|c': R})
    assert result['A'] == dict(under_call=dict(x=0, n=1), over_call=dict(x=1, n=2))
    assert result['B'] == dict(under_call=dict(x=1, n=1), over_call=dict(x=0, n=2))
    assert result['C'] == dict(under_call=dict(x=1, n=1), over_call=dict(x=1, n=2))


def test_audit_gaps_include_cells_outside_dev(tmp_path):
    write_world(tmp_path)
    inputs = E.load_inputs(tmp_path)
    dev_jids = {entry['j_id'] for entry in inputs['entries'][:10]}
    outside = [cid for cid in inputs['hariom'] if cid.split('|', 1)[0] not in dev_jids]
    assert outside
    rows = copy.deepcopy(inputs['merged'])
    cid = next(cid for cid in outside if inputs['hariom'][cid] == M)
    next(row for row in rows if row['cell_id'] == cid)['labels']['B'] = N
    result = E.audit_gaps(rows, inputs['hariom'])
    assert result['B']['under_call'] == dict(x=1, n=76)
    assert result['B']['over_call']['n'] == 74


@pytest.mark.parametrize('bad', [None, 'INVALID-PRIVATE-LABEL'])
def test_invalid_audit_judge_label_exits_two_without_outputs(tmp_path, bad):
    def mutate(world):
        cid = E.cell_id(world['audit'][0]['j_id'], world['audit'][0]['item'])
        next(row for row in world['merged'] if row['cell_id'] == cid)['labels']['A'] = bad
    write_world(tmp_path, mutate=mutate)
    assert go(tmp_path) == (2, ['audit_judge_labels count=1'])
    assert exported(tmp_path) == []


def test_invalid_audit_judge_labels_count_pairs(tmp_path):
    def mutate(world):
        cid = E.cell_id(world['audit'][0]['j_id'], world['audit'][0]['item'])
        row = next(row for row in world['merged'] if row['cell_id'] == cid)
        row['labels']['A'] = None
        row['labels']['B'] = 'INVALID'
    write_world(tmp_path, mutate=mutate)
    assert go(tmp_path) == (2, ['audit_judge_labels count=2']) and exported(tmp_path) == []


def test_e1_e2_equal_locked_report_on_fakes(tmp_path):
    write_world(tmp_path)
    draw, friend, merged, hariom = Q.load_report_inputs(tmp_path)
    report = Q.build_report(draw, friend, merged, hariom)
    assert go(tmp_path)[0] == 0
    summary = json.loads((tmp_path / E.SUMMARY_REL).read_bytes())
    assert summary['e1_input'] == report['e1_input'] and summary['e2_pool'] == report['e2_pool']
    # One error in 50 sampled from 222 unaudited mandatory cells weighs 222/50; divide by 298 mandatory cells.
    assert summary['e1_input']['weighted_binary_rate_on_final_mandatory'] == pytest.approx((222 / 50) / 298)
    assert summary['e2_pool'] == dict(x=1, n=126)


@pytest.mark.parametrize('kind', ['rate', 'population', 'pool', 'undefined'])
def test_checkpoint_mismatch_or_undefined_stops_before_writes(tmp_path, monkeypatch, kind):
    write_world(tmp_path)
    original = Q.build_report
    def different(*args):
        report = original(*args)
        if kind == 'rate':
            report['e1_input']['weighted_binary_rate_on_final_mandatory'] = .02
        elif kind == 'population':
            report['e1_input']['population'] = 299
        elif kind == 'pool':
            report['e2_pool']['x'] = 2
        else:
            report['e1_input']['undefined'] = True
        return report
    monkeypatch.setattr(Q, 'build_report', different)
    code, lines = go(tmp_path)
    assert code == 1 and 'label_quality_checkpoint count=1' in lines and exported(tmp_path) == []


def test_locked_report_guard_failure_stops_export(tmp_path, monkeypatch):
    write_world(tmp_path)
    original = Q.build_report
    def stopped(*args):
        report = original(*args)
        report['p21']['exit_code'] = 6
        return report
    monkeypatch.setattr(Q, 'build_report', stopped)
    assert go(tmp_path) == (6, ['label_quality_guard count=1']) and exported(tmp_path) == []


def test_manifest_matches_outputs_without_identifiers_or_labels(tmp_path):
    world = write_world(tmp_path)
    assert go(tmp_path)[0] == 0
    text = (tmp_path / E.MANIFEST_REL).read_text(encoding='utf-8')
    manifest = json.loads(text)
    assert set(manifest['outputs']) == set(E.OUTPUTS) - {E.MANIFEST_REL}
    for rel, info in manifest['outputs'].items():
        assert info['sha256_lf'] == normalised_sha((tmp_path / rel).read_bytes())
    for rel, sha in manifest['input_shas'].items():
        assert sha == normalised_sha((tmp_path / rel).read_bytes())
    assert not any(row['id'] in text for row in world['profiles'])
    assert not any(entry['j_id'] in text for entry in world['entries'])
    assert not any(label in text for label in E.LABELS)


def test_unmatched_content_never_appears_in_outputs(tmp_path):
    world = write_world(tmp_path)
    code, lines = go(tmp_path)
    assert code == 0
    text = '\n'.join(lines) + '\n'.join((tmp_path / rel).read_text(encoding='utf-8') for rel in E.OUTPUTS)
    for entry in world['entries'][10:]:
        assert entry['profile']['device_name'] not in text and entry['j_id'] not in text
    assert not any(entry['j_id'] in text for entry in world['entries'])
    assert 'unmatched_entries=20' in '\n'.join(lines)


@pytest.mark.parametrize('rel', [JF.DEV_REL, JF.JUDGE_REL])
def test_malformed_input_errors_do_not_echo_content(tmp_path, rel):
    write_world(tmp_path, malformed=rel)
    code, lines = go(tmp_path)
    assert code == 2 and exported(tmp_path) == [] and all('PRIVATE' not in line for line in lines)


def test_input_change_before_write_is_refused(tmp_path, monkeypatch):
    write_world(tmp_path)
    original = E.render_outputs
    def changed(*args):
        payloads = original(*args)
        args[0]['input_shas'][JF.DEV_REL] = '0' * 64
        return payloads
    monkeypatch.setattr(E, 'render_outputs', changed)
    assert go(tmp_path)[0] == 1 and exported(tmp_path) == []


def test_routed_merge_is_refused(tmp_path):
    def mutate(world):
        world['merged'][0]['routed'] = True
    write_world(tmp_path, mutate=mutate)
    assert go(tmp_path) == (1, ['merge_routed count=1']) and exported(tmp_path) == []


def test_forbidden_names_match_existing_sealed_constants():
    assert E.FORBIDDEN_FILE_NAMES == AF.SEALED_NAMES | {'.env'}
    assert Path(JF.MAP_REL).name in E.FORBIDDEN_FILE_NAMES
    assert Path(JF.MAP_REL).stem in E.FORBIDDEN_FILE_NAMES
    assert Path(JF.HOLDOUT_REL).name == PC.HOLDOUT_NAME
    assert PC.HOLDOUT_NAME in E.FORBIDDEN_FILE_NAMES


def test_success_reads_only_declared_inputs(tmp_path, monkeypatch):
    write_world(tmp_path)
    opened = set()

    def record(real):
        def reader(file, mode='r', *args, **kwargs):
            if isinstance(file, (str, os.PathLike)) and ('r' in mode or '+' in mode):
                opened.add(Path(file).resolve())
            return real(file, mode, *args, **kwargs)
        return reader

    with monkeypatch.context() as patch:
        patch.setattr(builtins, 'open', record(builtins.open))
        patch.setattr(io, 'open', record(io.open))
        code, _ = go(tmp_path)
    assert code == 0 and opened
    assert opened <= {(tmp_path / rel).resolve() for rel in E.INPUTS}


LOADER_ERROR_TYPES = (
    OSError, PermissionError, FileNotFoundError, FileExistsError, IsADirectoryError, NotADirectoryError,
    UnicodeError, UnicodeDecodeError, ValueError, json.JSONDecodeError, yaml.YAMLError, csv.Error,
    KeyError, IndexError, TypeError, AttributeError, OverflowError, ZeroDivisionError,
    RuntimeError, RecursionError, MemoryError,
    E.D.DrawError, Q.QualityError, SR.FriendError, E.LF.FreezeError, JF.JudgeFileError,
    SystemExit,
)
LOCKED_LOADERS = ((E.D, 'load_audit_rows'), (E.D, 'load_merge'), (Q, 'load_report_inputs'),
                  (JF, 'parse_eval'), (JF, 'load_parts'))


@pytest.mark.parametrize('loader', LOCKED_LOADERS, ids=[name for _, name in LOCKED_LOADERS])
@pytest.mark.parametrize('error_type', LOADER_ERROR_TYPES, ids=lambda cls: cls.__name__)
def test_loader_errors_are_sanitized_before_writes(tmp_path, monkeypatch, capsys, loader, error_type):
    write_world(tmp_path)
    calls, writes = [], []
    message = 'PRIVATE-LOADER-ERROR-CONTENT'
    if error_type is UnicodeDecodeError:
        error = error_type('utf-8', b'PRIVATE', 0, 1, message)
    elif error_type is json.JSONDecodeError:
        error = error_type(message, 'PRIVATE', 0)
    else:
        error = error_type(message)

    def fail(*args, **kwargs):
        calls.append(True)
        raise error

    def unexpected_write(*args, **kwargs):
        writes.append(True)
        raise AssertionError('output attempted after loader failure')

    monkeypatch.setattr(*loader, fail)
    monkeypatch.setattr(E, 'write_once', unexpected_write)
    assert go(tmp_path) == (2, ['input_or_output_validation count=1'])
    assert calls == [True] and writes == [] and exported(tmp_path) == []
    captured = capsys.readouterr()
    assert captured.out == '' and captured.err == ''
