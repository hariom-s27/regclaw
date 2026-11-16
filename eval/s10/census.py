"""Read-only S10 merge census. Counts only; never cell IDs or per-cell labels.

Later run: python -m eval.s10.census
Exit codes: 0 reported, 2 input error. No output files are created.
"""
import argparse
from collections import Counter
from pathlib import Path
import sys

from . import draws as D
from . import second_rater as SR
from .label_quality import QualityError, checked_merge_rows
from .merge import JUDGES, LABELS, cell_id

ROOT = Path(__file__).resolve().parents[2]


def census_counts(rows, audit_rows):
    rows = checked_merge_rows(rows)
    audit = sorted(audit_rows, key=lambda row: int(row['order']))
    if [int(row['order']) for row in audit] != list(range(1, len(audit) + 1)):
        raise QualityError('audit positions check failed')
    audited = [cell_id(row['j_id'], row['item']) for row in audit[:SR.AUDITED_PREFIX]]
    if len(audited) != len(set(audited)) or not set(audited) <= {row['cell_id'] for row in rows}:
        raise QualityError('audit membership check failed')
    audited = set(audited)
    overall = dict.fromkeys(LABELS, 0)
    prefix = dict.fromkeys(LABELS, 0)
    odd = dict.fromkeys(JUDGES, 0)
    patterns = Counter()
    splits = prefix_splits = all_high = three_way = 0
    for row in rows:
        overall[row['final']] += 1
        if row['cell_id'] in audited:
            prefix[row['final']] += 1
        votes = Counter(row['labels'].values())
        if len(votes) == 1:
            continue
        splits += 1
        prefix_splits += row['cell_id'] in audited
        patterns[tuple(votes[label] for label in LABELS)] += 1
        all_high += all(row['confidences'][judge] == 'high' for judge in JUDGES)
        if len(votes) == len(JUDGES):
            three_way += 1
        else:
            for judge in JUDGES:
                odd[judge] += votes[row['labels'][judge]] == 1
    return dict(cells=len(rows), merged_classes=overall, audited_cells=len(audited),
                audited_classes=prefix, split_cells=splits, audited_split_cells=prefix_splits,
                split_patterns=dict(patterns), odd_judge_out=odd,
                all_high_split_cells=all_high, three_way_splits=three_way)


def census_lines(counts):
    lines = [f'cells={counts["cells"]} audited_cells={counts["audited_cells"]}']
    for label in LABELS:
        lines.append(f'class={label} overall={counts["merged_classes"][label]} '
                     f'audited={counts["audited_classes"][label]}')
    lines.append(f'K_split_cells={counts["split_cells"]} audited_split_cells={counts["audited_split_cells"]}')
    lines.append('split_pattern_order=not_applicable,recommended,mandatory')
    for pattern, count in sorted(counts['split_patterns'].items()):
        lines.append(f'split_pattern={pattern} count={count}')
    for judge in JUDGES:
        lines.append(f'odd_judge_out={judge} count={counts["odd_judge_out"][judge]}')
    lines.append(f'all_high_split_cells={counts["all_high_split_cells"]} three_way_splits={counts["three_way_splits"]}')
    return lines


def main(argv=None, *, root=ROOT, out=print, err=None):
    err = err or (lambda message: print(message, file=sys.stderr))
    argparse.ArgumentParser(prog='python -m eval.s10.census', description=__doc__).parse_args(argv)
    try:
        rows = checked_merge_rows(D.load_merge(root))
        counts = census_counts(rows, D.load_audit_rows(root))
        lines = census_lines(counts)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError) as exc:
        if isinstance(exc, SR.FriendError) and str(exc).startswith('REFUSED:'):
            err(str(exc))
        else:
            err('ERROR: census input check failed; nothing written')
        return 2
    for line in lines:
        out(line)
    return 0


if __name__ == '__main__':
    sys.exit(main())
