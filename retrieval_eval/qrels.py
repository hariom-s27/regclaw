"""Strict TSV judging records with grades 0..3 and binary relevance >=2."""
import csv
from datetime import date
from pathlib import Path

FIELDS = ('qid', 'clause_id', 'grade', 'judge', 'date')


def validate(rows):
    result, seen = [], set()
    for row in rows:
        if set(row) != set(FIELDS):
            raise ValueError('qrels fields must be qid, clause_id, grade, judge, date')
        if str(row['grade']) not in ('0', '1', '2', '3'):
            raise ValueError('qrels grade must be 0..3')
        if any(not isinstance(row[k], str) or not row[k].strip() for k in ('qid', 'clause_id', 'judge', 'date')):
            raise ValueError('qrels identifiers, judge and date must be filled')
        date.fromisoformat(row['date'])
        key = row['qid'], row['clause_id']
        if key in seen:
            raise ValueError('Duplicate qrels pair; adjudicate before scoring')
        seen.add(key)
        result.append({**row, 'grade': int(row['grade'])})
    return result


def read(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if reader.fieldnames != list(FIELDS):
            raise ValueError('Invalid qrels.tsv header')
        return validate(list(reader))


def write(path, rows):
    rows = validate(rows)
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def grades_by_query(rows):
    grouped = {}
    for row in validate(rows):
        grouped.setdefault(row['qid'], {})[row['clause_id']] = row['grade']
    return grouped


def relevant(grades):
    return {cid for cid, grade in grades.items() if grade >= 2}
