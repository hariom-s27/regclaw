"""S8 Part D2c interleaved audit order + gap-free prefix freeze (s8_judging): fakes only, no network."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import pytest

from retrieval_eval import s8_judging as j
from retrieval_eval.s8_labels import LABEL_FIELDS
from retrieval_eval.s8_runs import sheet_csv
from tests.test_s8_labels import label, make_sheet, write_labels

QIDS = ['R1', 'R2', 'Q1', 'Q2', 'Q10']


def audit_folder(tmp_path, per_qid):
    """A fake audit folder: audit_sheet.csv + audit_ids.txt (sheet order), like s8-audit-sample writes."""
    folder = tmp_path / 'audit'
    folder.mkdir()
    rows = make_sheet(folder / 'audit_sheet.csv', per_qid)
    (folder / 'audit_ids.txt').write_text(''.join(r['item_id'] + '\n' for r in rows), encoding='utf-8')
    return folder, rows


def id_qid(rows):
    return [(r['item_id'], r['qid']) for r in rows]


# ---------------------------------------------------------------- order
def test_order_is_round_robin_in_query_order_and_skips_exhausted(tmp_path):
    rows = make_sheet(tmp_path / 's.csv', {'R1': 3, 'R2': 1, 'Q1': 4, 'Q2': 2, 'Q10': 3})
    qid_of = dict(id_qid(rows))
    order = j.audit_order(id_qid(rows), QIDS)
    assert sorted(order) == sorted(qid_of) and len(order) == 13
    assert [qid_of[i] for i in order] == ['R1', 'R2', 'Q1', 'Q2', 'Q10',  # round 1
                                          'R1', 'Q1', 'Q2', 'Q10',        # R2 ran out
                                          'R1', 'Q1', 'Q10',              # Q2 ran out
                                          'Q1']


def test_order_is_deterministic_seeded_and_input_order_free(tmp_path):
    rows = make_sheet(tmp_path / 's.csv', {q: 9 for q in QIDS})
    a = j.audit_order(id_qid(rows), QIDS)
    assert a == j.audit_order(id_qid(rows), QIDS) == j.audit_order(id_qid(rows)[::-1], QIDS)
    assert a != j.audit_order(id_qid(rows), QIDS, seed=1)
    within = [i for i in a if i.startswith('S8-q1')]
    assert within != sorted(within)  # shuffled within the query
    assert j.ORDER_SEED == 20261021


def test_order_unknown_qid_refused(tmp_path):
    rows = make_sheet(tmp_path / 's.csv', {'Q1': 2, 'Q99': 1})
    with pytest.raises(ValueError, match='Q99'):
        j.audit_order(id_qid(rows), ['Q1'])


def test_any_prefix_spreads_evenly(tmp_path):
    sizes = {'R1': 13, 'R2': 11, 'R3': 10, 'Q1': 11, 'Q2': 17, 'Q3': 13, 'Q4': 11, 'Q5': 8, 'Q6': 18,
             'Q7': 17, 'Q8': 8, 'Q9': 10, 'Q10': 17, 'Q11': 16, 'Q12': 11, 'Q13': 9}  # the c2 audit counts
    rows = make_sheet(tmp_path / 's.csv', sizes)
    qid_of = dict(id_qid(rows))
    order = j.audit_order(id_qid(rows), list(sizes))
    first = j.prefix_counts(order, qid_of, 100)
    assert sum(first.values()) == 100 and set(first.values()) == {6, 7}
    assert [q for q, c in first.items() if c == 7] == ['R1', 'R2', 'R3', 'Q1']  # queries.yaml order wins ties
    for n in (16, 50, 128):
        c = Counter(qid_of[i] for i in order[:n])
        assert max(c.values()) - min(c[q] for q in sizes) <= 1


def test_queries_order_is_queries_yaml_order():
    assert j.queries_order() == ['R1', 'R2', 'R3'] + [f'Q{k}' for k in range(1, 14)]


def test_run_audit_order_reads_id_and_qid_only_and_is_write_once(tmp_path):
    folder, rows = audit_folder(tmp_path, {'R1': 4, 'Q1': 3})
    res = j.run_audit_order(folder, ['R1', 'Q1'])
    text = (folder / j.AUDIT_ORDER).read_bytes()
    assert b'\r' not in text and text.endswith(b'\n')
    assert res['state'] == 'written' and res['items'] == 7 and res['sha256'] == j.bytes_sha(folder / j.AUDIT_ORDER)
    assert res['first_100_per_query'] == {'R1': 4, 'Q1': 3}
    poisoned = [{**r, 'text': 'OTHER', 'title': 'x', 'clause_id': 'y', 'source_doc': 'z', 'grade': '3'} for r in rows]
    (folder / 'audit_sheet.csv').write_text(sheet_csv(poisoned), encoding='utf-8', newline='')
    assert j.run_audit_order(folder, ['R1', 'Q1'])['state'] == 'unchanged'  # other columns do not matter
    (folder / 'audit_ids.txt').write_text('S8-x\n', encoding='utf-8')
    with pytest.raises(ValueError, match='differ'):
        j.run_audit_order(folder, ['R1', 'Q1'])


def test_run_audit_order_never_overwrites(tmp_path):
    folder, _ = audit_folder(tmp_path, {'R1': 4, 'Q1': 3})
    j.run_audit_order(folder, ['R1', 'Q1'])
    with pytest.raises(FileExistsError):
        j.run_audit_order(folder, ['Q1', 'R1'])


# ---------------------------------------------------------------- prefix freeze
@pytest.fixture
def ordered(tmp_path):
    folder, rows = audit_folder(tmp_path, {'R1': 60, 'Q1': 50, 'Q2': 40})
    j.run_audit_order(folder, ['R1', 'Q1', 'Q2'])
    order = (folder / j.AUDIT_ORDER).read_text(encoding='utf-8').split()
    return folder, order


def stamped(ids, start=datetime(2026, 10, 22, 9, tzinfo=timezone.utc), step=30):
    return [label(i, 1) | {'timestamp': (start + timedelta(seconds=step * k)).isoformat(timespec='seconds')}
            for k, i in enumerate(ids)]


def _write(folder, rows):
    return write_labels(folder / j.AUDIT_LABELS, rows, [*LABEL_FIELDS, 'timestamp'])


def test_freeze_accepts_gap_free_prefix_and_records_n(ordered):
    folder, order = ordered
    labels = _write(folder, stamped(order[:120]))
    rec = j.freeze_audit(labels, folder, folder / j.AUDIT_ORDER, now=datetime(2026, 10, 23, tzinfo=timezone.utc))
    assert json.loads((folder / 'audit_freeze.json').read_text(encoding='utf-8')) == rec
    assert rec['n'] == 120 and rec['minimum'] == 50 and rec['order_items'] == 150
    assert rec['order_sha256'] == j.bytes_sha(folder / j.AUDIT_ORDER) and rec['order_file'] == j.AUDIT_ORDER
    assert rec['labels_sha256'] == j.bytes_sha(labels)
    assert rec['audit_sheet_sha256'] == j.bytes_sha(folder / 'audit_sheet.csv')
    assert rec['audit_ids_sha256'] == j.bytes_sha(folder / 'audit_ids.txt')
    assert rec['frozen_at'] == '2026-10-23T00:00:00+00:00' and rec['median_seconds_per_item'] == 30
    assert rec['timestamps'] == 120
    assert j.frozen_prefix(folder / 'audit_freeze.json') == order[:120]
    with pytest.raises(FileExistsError):
        j.freeze_audit(labels, folder, folder / j.AUDIT_ORDER)


def test_freeze_refuses_gaps(ordered):
    folder, order = ordered
    labels = _write(folder, stamped(order[:50] + order[51:110]))  # position 51 skipped
    with pytest.raises(ValueError, match='position 51 is unlabelled'):
        j.freeze_audit(labels, folder, folder / j.AUDIT_ORDER)
    labels = _write(folder, stamped(order[1:120]))  # position 1 skipped
    with pytest.raises(ValueError, match='position 1 is unlabelled'):
        j.freeze_audit(labels, folder, folder / j.AUDIT_ORDER)
    assert not (folder / 'audit_freeze.json').exists()


def test_freeze_refuses_n_below_50_and_accepts_50(ordered):
    folder, order = ordered
    labels = _write(folder, stamped(order[:49]))
    with pytest.raises(ValueError, match='only 49 item.*minimum is 50'):
        j.freeze_audit(labels, folder, folder / j.AUDIT_ORDER)
    assert not (folder / 'audit_freeze.json').exists()
    fifty = _write(folder, stamped(order[:50]))
    rec = j.freeze_audit(fifty, folder, folder / j.AUDIT_ORDER)
    assert rec['n'] == 50 and rec['minimum'] == j.AUDIT_N_MIN == 50
    assert j.frozen_prefix(folder / 'audit_freeze.json') == order[:50]


def test_freeze_records_the_committed_partD_freeze_sha(ordered, tmp_path):
    import hashlib
    folder, order = ordered
    rec = j.freeze_audit(_write(folder, stamped(order[:50])), folder, folder / j.AUDIT_ORDER)
    committed = j.PARTD_FREEZE.read_bytes().replace(b'\r\n', b'\n')
    assert rec['partD_freeze_file'] == 's8_partD_freeze.json'
    assert rec['partD_freeze_lf_sha256'] == hashlib.sha256(committed).hexdigest()
    assert json.loads(committed)['audit_minimum_n_in_force'] == 50
    other = tmp_path / 'other'  # a CRLF copy gives the same (LF-normalised) sha
    other.mkdir()
    crlf = other / 's8_partD_freeze.json'
    crlf.write_bytes(committed.replace(b'\n', b'\r\n'))
    folder2, _ = audit_folder(other, {'R1': 30, 'Q1': 30})
    j.run_audit_order(folder2, ['R1', 'Q1'])
    order2 = (folder2 / j.AUDIT_ORDER).read_text(encoding='utf-8').split()
    rec2 = j.freeze_audit(_write(folder2, stamped(order2[:50])), folder2, folder2 / j.AUDIT_ORDER, partd_freeze=crlf)
    assert rec2['partD_freeze_lf_sha256'] == rec['partD_freeze_lf_sha256']


def test_freeze_refuses_invalid_fields_and_bad_order(ordered):
    folder, order = ordered
    rows = stamped(order[:100])
    rows[40]['unsure'] = 'maybe'
    with pytest.raises(ValueError, match='unsure'):
        j.freeze_audit(_write(folder, rows), folder, folder / j.AUDIT_ORDER)
    bad = folder / 'other_order.txt'
    bad.write_text(''.join(i + '\n' for i in order[:-1]), encoding='utf-8')
    with pytest.raises(ValueError, match='permutation'):
        j.freeze_audit(_write(folder, stamped(order[:100])), folder, bad)
    bad.write_text(''.join(i + '\n' for i in order + order[:1]), encoding='utf-8')
    with pytest.raises(ValueError, match='duplicate'):
        j.freeze_audit(_write(folder, stamped(order[:100])), folder, bad)


def test_frozen_prefix_refuses_a_changed_order_file(ordered):
    folder, order = ordered
    j.freeze_audit(_write(folder, stamped(order[:100])), folder, folder / j.AUDIT_ORDER)
    (folder / j.AUDIT_ORDER).write_text(''.join(i + '\n' for i in order[::-1]), encoding='utf-8')
    with pytest.raises(PermissionError, match='sha mismatch'):
        j.frozen_prefix(folder / 'audit_freeze.json')


def test_median_save_gap_sorts_timestamps_by_time():
    t = ['2026-10-22T09:00:00+00:00', '2026-10-22T09:01:00+00:00', '2026-10-22T09:00:20+00:00',
         '2026-10-23T18:00:00+00:00', '']  # a re-label + a break between sittings + one missing
    assert j.median_save_gap(t) == 40  # gaps 20, 40, ~1 day -> median 40
    assert j.median_save_gap(['2026-10-22T09:00:00+00:00']) is None


def test_freeze_cli_defaults_to_the_audit_order():
    import inspect
    src = inspect.getsource(j.main)
    assert "'--order', default=str(out / 'audit' / AUDIT_ORDER)" in src
    assert 'minimum' not in src  # the CLI never lowers N_min
