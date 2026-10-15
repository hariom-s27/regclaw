"""Offline official-structure scoring; never imports or changes the PDF parser.

Mappings (S3/S3-9 and the frozen sources.yaml): EPA sections 63.360--368
become §63.36N; first letter paragraphs are separate, deeper enumerations
merge into that letter. Appendix A decimal paths keep three decimal levels
(the frozen cap is 2 + appendix_decimal_levels = 4 including the root).
Tables 1--6 are entire top-level units. EU keeps Articles 1--10, paragraphs
and letter points; Roman subpoints merge into the letter. Annexes I/II/III
keep decimals to total depth 5; Chapters there are grouping titles only.
XIV/XV Part/Chapter tokens form a Roman prefix (Annex XV.II.2.3), with the
same total cap 5. Annex letter/Roman points merge into their decimal unit.
Empty grouping units are removed and parents reattached to the nearest
retained ancestor. Explicit titles and short decimal captions ending in a
full stop (up to four words, without a modal verb) are title-only. Only own
body text is scored, never descendant text.

P/R intervals use Wilson 95%. F1 bounds are the harmonic means of the
Wilson P/R bounds (Wilson-derived bounds, not a binomial model for F1).
Coverage is ordered character overlap / official characters after NFKC,
quote/dash/case/whitespace normalisation; an empty official body scores 1.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
import unicodedata
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def normalise(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    text = text.translate(str.maketrans({'“': '"', '”': '"', '‘': "'", '’': "'",
                                       '–': '-', '—': '-', '−': '-', '\u00ad': ''}))
    return ' '.join(text.split())


def unit(doc, number, parent, text):
    return dict(official_id=f'{doc}:{number}', number=number,
                parent=f'{doc}:{parent}' if parent else None, text=text.strip())


def project(units, policy_ids=None):
    """Merge capped units and repair parents.

    Raw removes empty headings. Policy retains official empty headings only
    when their ids occur in the parser output. Unknown extras remain errors.
    This changes the scoring projection, never the frozen clause data.
    """
    out = {}
    for u, keep in units:
        if keep:
            key = u['official_id']
            if key in out:
                raise ValueError(f'Duplicate official id: {key}')
            out[key] = dict(u)
        else:
            parent = u['parent']
            if parent not in out:
                raise ValueError(f'No capped ancestor for {u["official_id"]}')
            out[parent]['text'] += '\n' + u['text']
    parents = {k: v['parent'] for k, v in out.items()}
    out = {k: v for k, v in out.items() if normalise(v['text']) or k in (policy_ids or ())}
    for v in out.values():
        while v['parent'] and v['parent'] not in out:
            v['parent'] = parents[v['parent']]
    return list(out.values())


def parse_ecfr(path, *, policy_ids=None):
    root = ET.fromstring(Path(path).read_bytes())
    units = []
    for el in root.iter():
        kind = el.attrib.get('TYPE', '').upper()
        head = next((c for c in el if c.tag.rsplit('}', 1)[-1] == 'HEAD'), None)
        title = ' '.join(head.itertext()) if head is not None else ''
        number = el.attrib.get('N', '')
        section = re.search(r'63\.36[0-8]\b', number or title)
        if kind == 'SECTION' and section:
            base = '§' + section[0]
        elif kind == 'APPENDIX' and re.search(r'Appendix A\b', title, re.I):
            base = 'Appendix A'
        elif kind in {'TABLE', 'APPENDIX'} and (m := re.search(r'Table ([1-6])\b', title, re.I)):
            base = 'Table ' + m[1]
        else:
            continue
        units.append((unit('epa-63o', base, None, ''), True))
        current = len(units) - 1
        letter = None
        children = list(el)
        for child_index, child in enumerate(children):
            if child is head:
                continue
            text = ' '.join(' '.join(child.itertext()).split())
            if not text or text.startswith('[Source:') or re.match(r'^\[\d+ FR', text):
                continue
            if base.startswith('Table '):
                units[current][0]['text'] += '\n' + text
                continue
            if base.startswith('§') and (m := re.match(r'^\(([a-z])\)', text)):
                # Multi-letter Romans and numbered subparagraphs stay in current.
                # (i) after (h) is a sibling; after (a) it is a deeper Roman.
                token = m[1]
                following = ' '.join(children[child_index+1].itertext()).strip() if child_index+1 < len(children) else ''
                roman_next = {'i': 'ii', 'v': 'vi', 'x': 'xi'}
                roman_child = token in 'ivx' and letter and (
                    ord(token) != ord(letter) + 1 or
                    following.startswith('(' + roman_next[token] + ')'))
                if not roman_child:
                    letter = token
                    units.append((unit('epa-63o', base + '(' + token + ')', base, text), True))
                    current = len(units) - 1
                    continue
            if base == 'Appendix A' and (m := re.match(r'^(\d+(?:\.\d+)*)(?:\.?\s|$)', text)):
                token = m[1]
                parts = token.split('.')
                capped = '.'.join(parts[:3])
                num = base + '.' + capped
                if len(parts) <= 3:
                    parent = base + '.' + '.'.join(parts[:-1]) if len(parts) > 1 else base
                    # Decimal intermediate headings can be absent in the source.
                    known = {u['number'] for u, _ in units}
                    while parent not in known:
                        parent = parent.rsplit('.', 1)[0]
                    # Official HD elements are titles, not own body text.
                    italic = [c for c in child if c.tag.rsplit('}', 1)[-1] == 'I']
                    title_only = italic and normalise(text) == normalise(token + ' ' + ' '.join(italic[0].itertext()))
                    body = '' if child.tag.rsplit('}', 1)[-1].startswith('HD') or title_only else text
                    units.append((unit('epa-63o', num, parent, body), True))
                    current = len(units) - 1
                    continue
            units[current][0]['text'] += '\n' + text
    if not units:
        raise ValueError('No supported eCFR structural units found')
    return project(units, policy_ids)


@dataclass
class Node:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)

    def text(self):
        return ' '.join(c if isinstance(c, str) else c.text() for c in self.children)


class HTMLTree(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.root = Node('root')
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {'meta', 'link', 'br', 'img', 'hr', 'input', 'wbr', 'source'}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def html_blocks(node):
    if node.tag in {'script', 'style', 'head', 'nav'}:
        return
    if node.tag == 'tr':
        cells = [c for c in node.children if isinstance(c, Node) and c.tag in {'td', 'th'}]
        def nested_rows(n):
            return any(isinstance(c, Node) and (c.tag == 'tr' or nested_rows(c)) for c in n.children)
        if len(cells) >= 2 and nested_rows(node):
            label = ' '.join(cells[0].text().split())
            body = [b for c in cells[1:] for b in html_blocks(c)]
            if body:
                yield label + ' ' + body[0]
                yield from body[1:]
                return
    if node.tag == 'tr' or node.tag in {'p', 'h1', 'h2', 'h3', 'h4', 'h5'}:
        text = ' '.join(node.text().split())
        classes = node.attrs.get('class', '')
        if 'sti-art' in classes or ('ti-grseq' in classes and not re.match(r'^(?:\d|PART\b|CHAPTER\b)', text)):
            return
        if text:
            yield text
        return
    for child in node.children:
        if isinstance(child, Node):
            yield from html_blocks(child)


def parse_eurlex(path, *, policy_ids=None):
    blocks = html_blocks(HTMLTree(Path(path).read_text(encoding='utf-8-sig')).root)
    units, stack = [], []
    active = False
    base = group = para = point = None
    letter = None

    def add(number, depth, text):
        while stack and stack[-1][1] >= depth:
            stack.pop()
        parent = stack[-1][0] if stack else None
        units.append((unit('eu-mdr', number, parent, text), True))
        stack.append((number, depth))

    for text in blocks:
        if m := re.fullmatch(r'Article (\d+)', text, re.I):
            active = 1 <= int(m[1]) <= 10
            base, group, para, point, letter = 'Art ' + m[1], None, None, None, None
            stack.clear()
            if active:
                add(base, 1, '')
            continue
        if m := re.fullmatch(r'ANNEX ([IVX]+)', text, re.I):
            active = m[1].upper() in {'I', 'II', 'III', 'XIV', 'XV'}
            base, group, para, point, letter = 'Annex ' + m[1].upper(), None, None, None, None
            stack.clear()
            if active:
                add(base, 1, '')
            continue
        if not active:
            continue
        if m := re.match(r'^(?:PART|CHAPTER) ([A-Z]+|\d+)\b', text, re.I):
            if base in {'Annex XIV', 'Annex XV'}:
                group = base + '.' + m[1].upper()
                add(group, 2, '')
            continue
        if base.startswith('Art '):
            if m := re.match(r'^(?:\((\d+)\)|(\d+)\.)\s*(.*)', text):
                para = base + '(' + (m[1] or m[2]) + ')'
                point = letter = None
                add(para, 2, m[3])
                continue
            if para and (m := re.match(r'^\(([a-z]+)\)\s*(.*)', text)):
                token = m[1]
                roman = len(token) > 1 or (token in 'ivx' and letter and ord(token) != ord(letter) + 1)
                if not roman:
                    letter, point = token, para + '(' + token + ')'
                    add(point, 3, m[2])
                    continue
        elif m := re.match(r'^(\d+(?:\.\d+)*)(?:\.?\s|$)(.*)', text):
            prefix = group or base
            depth = (2 if group else 1) + len(m[1].split('.'))
            if depth <= 5:
                num = prefix + '.' + m[1]
                while stack and (stack[-1][1] >= depth or not num.startswith(stack[-1][0] + '.')):
                    stack.pop()
                body = m[2]
                if len(body.split()) <= 4 and body.endswith('.') and not re.search(r'\b(shall|should|must|means)\b', body, re.I):
                    body = ''
                add(num, depth, body)
                continue
        if units:
            units[-1][0]['text'] += '\n' + text
    if not units:
        raise ValueError('No supported EUR-Lex structural units found')
    return project(units, policy_ids)


def wilson(success, total):
    if not total:
        return [0.0, 1.0]
    p, z = success / total, 1.959963984540054
    denom = 1 + z*z/total
    mid = (p + z*z/(2*total)) / denom
    half = z*math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / denom
    return [max(0., mid-half), min(1., mid+half)]


def coverage(expected, actual):
    a, b = normalise(expected), normalise(actual)
    if a in b:
        return 1.
    return sum(m.size for m in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks()) / len(a) if a else 1.


def first_difference(expected, actual):
    """First differing 120-character windows in the normalised coverage text.

    Offsets refer to each normalised string, not PDF character positions. An
    empty window denotes the end of that string. Keep the first difference even
    if it is only a paragraph marker; likely causes consider all differences.
    """
    official, ours = normalise(expected), normalise(actual)
    differences = [op for op in SequenceMatcher(None, official, ours, autojunk=False).get_opcodes()
                   if op[0] != 'equal']
    if not differences:
        return None, differences
    operation, i, _, j, _ = differences[0]
    return dict(operation=operation, official_offset=i, ours_offset=j,
                official=official[i:i+120], ours=ours[j:j+120]), differences


def coverage_cause(official, ours, differences):
    """Evidence-based reporting hints, not definitive parser diagnoses."""
    a, b = normalise(official['text']), normalise(ours['text'])
    removed = ' '.join(a[i:j] for op, i, j, _, _ in differences if op in {'delete', 'replace'})
    causes = []
    title = normalise(ours.get('title', '')).rstrip('.:')
    if title and title in a and title not in b:
        causes.append('Title/body boundary: heading stored in our title but counted in the official body')
    if 'where:' in removed:
        causes.append('Equation extraction: variable definitions omitted or reordered in PDF text')
    if re.search(r'\(\s*\d+\s*\) (?:regulation|directive)', removed):
        causes.append('Footnote handling: official footnote text omitted or moved in PDF text')
    if ':Table ' in official['official_id']:
        causes.append('Table extraction: row/column order, headers and footnotes differ')
    if not causes:
        causes.append('Body truncation or source-version difference: numbered text absent; inspect paragraph boundaries')
    return '; '.join(causes)


def score(expected, clauses, doc, *, details=False):
    gold = {u['official_id']: u for u in expected}
    actual = {c['id']: c for c in clauses if c['doc'] == doc}
    matched = sorted(gold.keys() & actual.keys())
    tp = len(matched)
    precision = tp / len(actual) if actual else 0.
    recall = tp / len(gold) if gold else 0.
    harmonic = lambda a, b: 2*a*b/(a+b) if a+b else 0.
    pc, rc = wilson(tp, len(actual)), wilson(tp, len(gold))
    cover = {key: coverage(gold[key]['text'], actual[key]['text']) for key in matched}

    def finding(key, source, cause):
        return dict(id=key, preview=source[key]['text'][:120], likely_cause=cause)

    result = dict(doc=doc, expected=len(gold), actual=len(actual), matched=tp,
                precision=precision, precision_ci=pc, recall=recall, recall_ci=rc,
                f1=harmonic(precision, recall), f1_ci=[harmonic(*v) for v in zip(pc, rc)],
                parent_accuracy=sum(gold[k]['parent'] == actual[k]['parent'] for k in matched)/tp if tp else None,
                coverage=sum(cover.values())/tp if tp else None, coverage_by_id=cover,
                missing=[finding(k, gold, 'Missed heading, source-version difference, or parser filtering; inspect preview') for k in sorted(gold.keys()-actual.keys())],
                extra=[finding(k, actual, 'False heading, numbering/group mapping, or source-version difference; inspect preview') for k in sorted(actual.keys()-gold.keys())],
                low_coverage=[dict(id=k, coverage=v, preview=gold[k]['text'][:120]) for k, v in cover.items() if v < .9])
    if details:
        result['parent_mismatches'] = []
        for key in matched:
            expected_parent, ours_parent = gold[key]['parent'], actual[key]['parent']
            if expected_parent == ours_parent:
                continue
            grouping = ours_parent is not None and ours_parent not in gold
            cause = ('Parent projection: official scoring removes an empty/title-only grouping retained in our corpus'
                     if grouping else 'Parent hierarchy: parser attachment differs from projected official ancestry')
            result['parent_mismatches'].append(dict(
                id=key, expected_parent=expected_parent, ours_parent=ours_parent,
                official_preview=gold[key]['text'][:120], ours_preview=actual[key]['text'][:120],
                expected_parent_preview=gold.get(expected_parent, {}).get('text', '')[:120],
                ours_parent_preview=actual.get(ours_parent, {}).get('text', '')[:120], likely_cause=cause))
        for finding in result['low_coverage']:
            key = finding['id']
            difference, operations = first_difference(gold[key]['text'], actual[key]['text'])
            finding.update(first_difference=difference,
                           official_preview=gold[key]['text'][:120], ours_preview=actual[key]['text'][:120],
                           likely_cause=coverage_cause(gold[key], actual[key], operations))
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--details', action='store_true',
                        help='List every parent mismatch and low-coverage finding, grouped by likely cause.')
    args = parser.parse_args(argv)
    paths = [(ROOT/'data/gold_structure/ecfr_40cfr63_subpartO.xml', 'epa-63o', parse_ecfr),
             (ROOT/'data/gold_structure/eurlex_32017R0745.html', 'eu-mdr', parse_eurlex)]
    missing = [str(p) for p, _, _ in paths if not p.exists()]
    if missing:
        print('SKIP real scoring: missing inputs: ' + ', '.join(missing))
        return 0
    clauses = json.loads((ROOT/'data/clauses.json').read_text(encoding='utf-8'))
    for path, doc, parse in paths:
        content = path.read_text(encoding='utf-8-sig')
        metadata = re.findall(r'.{0,40}(?:5\.5\.2017|2017/745|89 FR 24172|Apr\. 5, 2024|_SUBSTITUTE_DATE_|current as of).{0,65}', content, re.I)
        result = score(parse(path), clauses, doc, details=args.details)
        excluded = {'coverage_by_id', 'missing', 'extra', 'parent_mismatches'}
        if args.details:
            excluded.add('low_coverage')
        summary = {k: v for k, v in result.items() if k not in excluded}
        print(json.dumps(dict(file=path.name, size=path.stat().st_size, version_evidence=metadata[:20], **summary), indent=2, ensure_ascii=False))
        print('Top 20 missing:', json.dumps(result['missing'][:20], ensure_ascii=False))
        print('Top 20 extra:', json.dumps(result['extra'][:20], ensure_ascii=False))
        # Keep the official raw view visible; structural extras are admitted
        # only by the independent official parse, not by the actual parents.
        actual_ids = {c['id'] for c in clauses if c['doc'] == doc}
        policy = score(parse(path, policy_ids=actual_ids), clauses, doc)
        structural = sorted({u['official_id'] for u in parse(path, policy_ids=actual_ids)}
                            - {u['official_id'] for u in parse(path)})
        print('Policy score:', json.dumps({k: policy[k] for k in
              ('doc', 'expected', 'actual', 'matched', 'precision', 'recall', 'f1', 'parent_accuracy')}, ensure_ascii=False))
        print('Structural extras (by design):', json.dumps(structural, ensure_ascii=False))
        if args.details:
            for kind in ('parent_mismatches', 'low_coverage'):
                grouped = {}
                for finding in result[kind]:
                    grouped.setdefault(finding['likely_cause'], []).append(finding)
                print(f'All {kind} ({len(result[kind])}), grouped by likely cause:')
                print(json.dumps(grouped, indent=2, ensure_ascii=False))
    return 0
