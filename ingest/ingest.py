"""Per-source parser v2. All inputs are explicit or manifest-listed."""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
import csv
import difflib
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import unicodedata

import pymupdf as fitz
import yaml

from .validate import quality_report, validate

ROOT = Path(__file__).resolve().parents[1]
PARSER_VERSION = '2.0'
TEXT_FLAGS = (fitz.TEXTFLAGS_DICT & ~fitz.TEXT_PRESERVE_LIGATURES &
              ~fitz.TEXT_PRESERVE_IMAGES) | fitz.TEXT_COLLECT_STYLES
DROP_FIELDS = ['doc', 'number', 'page', 'reason', 'detail', 'text_preview']
WARNING_FIELDS = ['doc', 'page', 'number', 'code', 'detail']


def clean(text):
    return unicodedata.normalize('NFKC', text.replace('\u00ad', ''))


def is_bold(span):
    font = re.sub(r'^[A-Z]{6}\+', '', span.get('font', ''))
    return bool(span.get('flags', 0) & 16 or
                re.search(r'bold|black|heavy|semibold|demi', font, re.I) or
                span.get('char_flags', 0) & 8)


@dataclass
class Line:
    text: str
    page: int = 1
    bbox: tuple = (0, 0, 0, 0)
    spans: list = field(default_factory=list)
    block: int = 0
    height: float = 800
    role: str = 'continuation'
    number: str = ''
    parent_number: str = ''
    line_id: str = ''
    table_text: str = ''

    @property
    def bold(self):
        first = next((s for s in self.spans if s.get('text', '').strip()), {})
        return is_bold(first)


def extract_lines(pdf, tables=False):
    """Keep content-stream order and metadata, including lines later filtered out."""
    lines, bookmarks = [], []
    with fitz.open(pdf) as document:
        bookmarks = document.get_toc()
        pages = len(document)
        for p, page in enumerate(document, 1):
            page_lines = []
            for bi, block in enumerate(page.get_text('dict', sort=False, flags=TEXT_FLAGS)['blocks']):
                for row in block.get('lines', []):
                    visible = [s for s in row['spans'] if 'char_flags' not in s or s['char_flags'] & (16 | 32)]
                    # Retain the original visible string for the pre-filter sample export.
                    text = clean(''.join(s['text'] for s in visible)).strip()
                    if text:
                        page_lines.append(Line(text, p, tuple(row['bbox']), visible, bi, page.rect.height))
            if tables:
                for table in page.find_tables().tables:
                    if table.row_count < 3 or table.col_count < 3:
                        continue
                    inside = [l for l in page_lines if fitz.Rect(table.bbox).contains(fitz.Rect(l.bbox))]
                    for l in inside:
                        l.role = 'table'
                    if inside:
                        inside[0].table_text = '\n'.join(' | '.join(clean(str(x or '')) for x in row)
                                                         for row in table.extract())
            for i, line in enumerate(page_lines):
                line.line_id = f'{p}:{i + 1}'
            lines.extend(page_lines)
    return lines, pages, bookmarks


def join_text(lines, vocabulary, stats=None):
    paragraphs = []
    for line in lines:
        if (line.text if isinstance(line, Line) else line) == '\u2029':
            paragraphs.append('')
            continue
        text = re.sub(r'\s+', ' ', clean(line.text if isinstance(line, Line) else line)).strip()
        if not text:
            continue
        if not paragraphs or not paragraphs[-1] or re.match(r'^(NOTE|EXAMPLE)\b', text) or re.match(r'^(NOTE|EXAMPLE)\b', paragraphs[-1]):
            paragraphs.append(text)
        elif paragraphs[-1].endswith('-'):
            prefix = re.search(r'([A-Za-z]+)-$', paragraphs[-1])
            suffix = re.match(r'([a-z][A-Za-z]*)', text)
            remove = prefix and suffix and (prefix[1] + suffix[1]).casefold() in vocabulary
            paragraphs[-1] = paragraphs[-1][:-1] + text if remove else paragraphs[-1] + text
            if stats is not None:
                stats['hyphen_joins'] += int(bool(remove))
        else:
            # Blank block boundaries are represented by a newline sentinel.
            if text == '\u2029':
                paragraphs.append('')
            else:
                paragraphs[-1] += ' ' + text
    return '\n'.join(p for p in paragraphs if p)


def toc_like(text, config):
    if re.search(r'\.{3,}\s*\d+\s*$|\s{4,}\d+\s*$', text):
        return True
    matches = [re.match(l['pattern'], text) for l in config['levels']]
    return bool(any(m and m.groupdict().get('title') for m in matches) and
                re.search(r'\S\s+\d{1,4}\s*$', text))


def band_key(text):
    text = text.casefold()
    text = re.sub(r'\b[ivxlcdm]+\b|\d+', '@', text)
    return re.sub(r'\s+', ' ', text).strip()


def filter_lines(lines, pages, config):
    by_page = defaultdict(list)
    for l in lines:
        by_page[l.page].append(l)
    sizes = Counter()
    for l in lines:
        for s in l.spans:
            if not s.get('flags', 0) & 1:
                sizes[round(s.get('size', 12), 1)] += len(s['text'])
    body_size = sizes.most_common(1)[0][0] if sizes else 12
    bands = {'top': {}, 'bottom': {}}
    for p, ls in by_page.items():
        bands['top'][p] = ls[:4]
        bands['bottom'][p] = ls[-4:]
    repeat = set()
    similarities = {}
    if pages >= 2:
        for band in bands.values():
            for p, ls in band.items():
                for l in ls:
                    if any(re.match(level['pattern'], l.text) for level in config['levels']):
                        continue
                    key = band_key(l.text)
                    for subset in (list(band), [q for q in band if q % 2 == p % 2]):
                        threshold = max(3, math.ceil(.3 * len(subset)))
                        def similar(other):
                            other_key = band_key(other.text)
                            pair = tuple(sorted((key, other_key)))
                            if pair not in similarities:
                                matcher = difflib.SequenceMatcher(None, key, other_key)
                                similarities[pair] = (matcher.quick_ratio() >= .7 and matcher.ratio() >= .7)
                            return similarities[pair]
                        hits = sum(any(similar(other)
                                       for other in band[q]) for q in subset)
                        if hits >= threshold:
                            repeat.add(l.line_id)
    blocks = defaultdict(list)
    for l in lines:
        blocks[(l.page, l.block)].append(l)
    footnotes = set()
    for ls in blocks.values():
        span_sizes = [s.get('size', 12) for l in ls for s in l.spans]
        if (ls[0].bbox[1] >= .75 * ls[0].height and span_sizes and
                statistics.median(span_sizes) <= .85 * body_size and
                re.match(r'^(\d+|[*†‡])', ls[0].text)):
            footnotes.update(l.line_id for l in ls)
    toc_pages = {p for p, ls in by_page.items()
                 if p <= max(config.get('toc_scan_pages', 10), math.ceil(.15 * pages)) and
                 sum(toc_like(l.text, config) for l in ls) / max(1, len(ls)) >= .4}
    counts = Counter()
    stripped_chars = footnote_chars = 0
    for l in lines:
        role = None
        if l.page in toc_pages:
            role = 'toc'
        elif re.fullmatch(r'\d{1,4}|Page\s+\d+\s+of\s+\d+|-\s*\d+\s*-', l.text, re.I):
            # A bare number ending in '.' is a heading; a bare integer is a page number.
            role = 'page_number'
        elif l.line_id in footnotes:
            role = 'footnote'
        elif re.match(r'^\[\d+ FR \d+', l.text):
            role = 'source_note'
        elif l.line_id in repeat or any(re.search(p, l.text) for p in config.get('strip_lines', [])):
            role = 'header_footer'
        elif l.page <= config.get('toc_scan_pages', 10) and toc_like(l.text, config):
            role = 'toc'
        if role:
            l.role = role
            counts[role] += 1
            stripped_chars += len(l.text) + 1
            if role == 'footnote':
                footnote_chars += len(l.text)
        elif l.role != 'table':
            l.text = clean(''.join(s['text'] for s in l.spans if not s.get('flags', 0) & 1)).strip() if l.spans else l.text
    return dict(counts), stripped_chars, footnote_chars, body_size


def resolve_ambiguous(token, open_values, x0=0, parent_x0=0):
    """R10 ambiguity priority: expected successor, first child, indent, letter."""
    letters = 'abcdefghijklmnopqrstuvwxyz'
    romans = ['i', 'ii', 'iii', 'iv', 'v', 'vi', 'vii', 'viii', 'ix', 'x']
    for kind, values in (('letter', letters), ('roman', romans)):
        prev = open_values.get(kind)
        if prev is not None and prev in values and values.index(prev) + 1 < len(values) and values[values.index(prev) + 1] == token:
            return kind, False
    if token == 'i' and 'letter' in open_values:
        return 'roman', False
    if token == 'a':
        return 'letter', False
    if len(token) > 1:
        return None, False
    if x0 > parent_x0 and token == 'i':
        return 'roman', False
    return 'letter', True


def heading(line, config, stack, previous=''):
    text = line.text.strip()
    if toc_like(text, config) or re.search(r'\b(see|under|with|in|of)\s*$', previous, re.I):
        return None
    if re.match(r'^\d+(?:\.\d+)*\.?\s+(of|in|and|or|to)\b', text, re.I):
        return None
    if re.match(r'^\([a-z0-9]+\)(?:\([a-z0-9]+\))*[),]*\s+(of|in|and|or|to)\b', text, re.I):
        return None
    mode = config['mode']
    if config.get('heading_font') and line.spans and not any(config['heading_font'] in s.get('font', '') for s in line.spans):
        return None
    matches = []
    for level in config['levels']:
        m = re.match(level['pattern'], text)
        if m:
            matches.append((level, m))
    if not matches:
        return None
    if mode == 'fda' and len(matches) > 1:
        # I. can mean the next letter after H., rather than Roman I.
        last_letter = next((s for s in reversed(stack) if s['_kind'] == 'letter'), None)
        if last_letter:
            for candidate in matches:
                token = candidate[1].group('number')
                prior = last_letter['number'].split('.')[-1]
                if candidate[0]['name'] == 'letter' and len(token) == len(prior) == 1 and ord(token) == ord(prior) + 1:
                    matches = [candidate]
                    break
    level, match = matches[0]
    if level.get('require_bold') and not line.bold:
        return None
    kind = level['name']
    token = match.group('number')
    title = (match.groupdict().get('title') or '').strip(' :–—')
    parent = stack[-1] if stack else None
    depth = level.get('depth', 1)
    number = token
    if kind in ('annex', 'attachment', 'appendix', 'schedule', 'article'):
        if kind == 'schedule':
            number = 'Schedule ' + str(['FIRST', 'SECOND', 'THIRD', 'FOURTH', 'FIFTH', 'SIXTH', 'SEVENTH', 'EIGHTH'].index(token) + 1)
        else:
            number = {'article': 'Art ', 'annex': 'Annex ', 'attachment': 'Attachment ', 'appendix': 'Appendix '}[kind] + token
        depth = 1
    elif mode == 'fda':
        if depth > 1:
            ancestor = next((s for s in reversed(stack) if s['_depth'] == depth - 1), None)
            if not ancestor:
                return None
            number = ancestor['number'] + '.' + token
    elif mode == 'eu':
        root = next((s for s in stack if s['_depth'] == 1), None)
        if not root:
            return None
        if root['number'].startswith('Art '):
            if kind == 'point':
                para = next((s for s in reversed(stack) if s['_depth'] == 2), None)
                if not para:
                    return None
                number, depth = para['number'] + '(' + token + ')', 3
            else:
                number, depth = root['number'] + '(' + token.strip('().') + ')', 2
        else:
            if kind == 'point':
                return {'depth': 99, 'number': token, 'title': title, 'kind': kind}
            number = root['number'] + '.' + token.rstrip('.')
            depth = 1 + len(token.rstrip('.').split('.'))
    elif mode == 'decimal':
        number = token.rstrip('.')
        if number.endswith('.0'):
            number = number[:-2]
        depth = len(number.split('.'))
        root = next((s for s in stack if s['_kind'] == 'appendix'), None)
        if root:
            number = root['number'] + '.' + number
            depth += 1
    elif mode == 'india':
        root = next((s for s in stack if s['_kind'] == 'schedule'), None)
        if kind == 'rule' and root:
            token = token[:-2] if token.endswith('.0') else token.rstrip('.')
            number, depth = root['number'] + '.' + token, 1 + len(token.split('.'))
        elif kind != 'rule':
            return {'depth': 99, 'number': token, 'title': title, 'kind': kind}
    elif mode == 'epa':
        if kind == 'section':
            number = '§' + token
        else:
            root = next((s for s in stack if s['_kind'] == 'section'), None)
            if not root:
                return None
            if kind == 'point':
                prior = next((s for s in reversed(stack) if s['_kind'] == 'point'), None)
                values = {'letter': re.search(r'\(([a-z])\)(?:~\d+)?$', prior['number'])[1]} if prior else {}
                reading, ambiguous = resolve_ambiguous(token, values, line.bbox[0], prior.get('_x0', 0) if prior else 0)
                if prior and line.bbox[0] > prior.get('_x0', 0) + 3 and not (values.get('letter') and
                        ord(token) == ord(values['letter']) + 1):
                    return {'depth': 99, 'number': token, 'title': title, 'kind': 'nested_point'}
                if token in ('i', 'v', 'x') and reading == 'roman':
                    return {'depth': 99, 'number': token, 'title': title, 'kind': 'roman_point'}
                number, depth = root['number'] + '(' + token + ')', 2
                if ambiguous and token in ('i', 'v', 'x', 'c', 'd', 'l', 'm'):
                    return {'depth': depth, 'number': number, 'title': title, 'kind': kind,
                            'ambiguous': True}
            else:
                return {'depth': 99, 'number': token, 'title': title, 'kind': kind}
    return {'depth': depth, 'number': number, 'title': title, 'kind': kind}


def parse_document(pdf, doc, config, slices=None):
    lines, pages, bookmarks = extract_lines(pdf, config.get('tables') == 'clause')
    raw_text = '\n'.join(l.text for l in lines)
    original = {l.line_id: l.text for l in lines}
    filters, stripped_chars, footnote_chars, body_size = filter_lines(lines, pages, config)
    vocabulary = set(re.findall(r'[A-Za-z]+', raw_text.casefold()))
    stats = {'pages': pages, 'raw_headings': 0, 'kept_clauses': 0, 'drops': {},
             'line_filters': filters, 'footnote_chars': footnote_chars, 'body_size': body_size,
             'hyphen_joins': 0, 'depth_merges': 0, 'bold_calibration': {}, 'clauses_per_level': {},
             'expected_clauses': config.get('expected_clauses')}
    calibration = defaultdict(lambda: [0, 0])
    stack, candidates, drops, warnings = [], [], [], []
    siblings = {}
    current = None
    marker_parts = (slices or {}).get('parts', [])
    active = not bool(marker_parts or config.get('body_start_marker'))
    part = 0
    outside = []
    used_ids = Counter()
    table_counts = Counter()
    previous = ''

    def log_drop(c, reason, detail=''):
        drops.append(dict(doc=doc, number=c.get('number', ''), page=c['page'], reason=reason,
                          detail=detail, text_preview=c.get('text', '')[:80]))

    def flush_outside():
        if outside:
            log_drop({'page': outside[0].page, 'text': ' '.join(l.text for l in outside)}, 'outside_markers')
            outside.clear()

    for i, line in enumerate(lines):
        if line.role in ('toc', 'header_footer', 'page_number', 'footnote', 'source_note'):
            continue
        if marker_parts:
            # Each slice can end before its stop marker; a later start resets the range.
            new_part = next((j for j, m in enumerate(marker_parts) if line.text.strip() == m['start_marker']), None)
            if new_part is not None and new_part > part:
                part = new_part
        if marker_parts and part < len(marker_parts):
            markers = marker_parts[part]
            if line.text.strip() == markers['start_marker']:
                active = True
                flush_outside()
                stack.clear()
                current = None
            elif line.text.strip() == markers['stop_marker']:
                active = False
                current = None
                stack.clear()
                part += 1
                continue
        elif config.get('body_start_marker') and re.search(config['body_start_marker'], line.text):
            active = True
            flush_outside()
        if config.get('body_stop_marker') and re.search(config['body_stop_marker'], line.text):
            active = False
        if not active:
            outside.append(line)
            line.role = 'noise'
            continue
        for lv in config['levels']:
            if re.match(lv['pattern'], line.text):
                calibration[lv['name']][0] += 1
                calibration[lv['name']][1] += int(line.bold)
                break
        if line.role == 'table':
            if line.table_text and current:
                parent = current
                table_counts[parent['number']] += 1
                num = parent['number'] + '.T' + str(table_counts[parent['number']])
                c = dict(id=f'{doc}:{num}', doc=doc, number=num, title=parent['title'] + ' (table)',
                         text=line.table_text, parent=parent['id'], source_pdf=Path(pdf).name,
                         page=line.page, informative=parent['informative'], _depth=parent['_depth'] + 1,
                         _kind='table', _body=[], _skip=parent['_skip'])
                candidates.append(c)
                line.number, line.parent_number = num, parent['number']
            continue
        h = heading(line, config, stack, previous)
        if h:
            if h.get('ambiguous'):
                warnings.append(dict(doc=doc, page=line.page, number=h['number'], code='AMBIGUOUS_NUM', detail='fallback to letter reading'))
            stats['raw_headings'] += 1
            cap = config['max_depth']
            if config['mode'] == 'india':
                cap = 2 if any(s['_kind'] == 'schedule' for s in stack) else 1
            if h['depth'] > cap:
                stats['depth_merges'] += 1
                h = None
        if h:
            depth = h['depth']
            while stack and stack[-1]['_depth'] >= depth:
                stack.pop()
            parent = stack[-1] if stack else None
            num = h['number']
            if parent and '.' in num and h['kind'] in ('decimal', 'digit', 'letter', 'small_letter') and not num.startswith(parent['number'] + '.'):
                warnings.append(dict(doc=doc, page=line.page, number=num, code='PARENT_PREFIX_MISMATCH', detail=parent['number']))
            key = (parent['id'] if parent else None, depth, h['kind'])
            tail = re.findall(r'\d+|[A-Za-z]+', num)[-1]
            seq = int(tail) if tail.isdigit() else None
            if h['kind'] == 'roman':
                roman_values = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X']
                seq = roman_values.index(tail) + 1 if tail in roman_values else None
            elif len(tail) == 1 and tail.isalpha():
                seq = ord(tail.casefold()) - ord('a') + 1
            if key in siblings and seq is not None:
                last = siblings[key]
                code = 'SIBLING_REPEAT' if seq == last else 'SIBLING_GAP' if seq != last + 1 else None
                if code:
                    warnings.append(dict(doc=doc, page=line.page, number=num, code=code, detail=f'{last} -> {seq}'))
            if seq is not None:
                siblings[key] = seq
            used_ids[num] += 1
            if used_ids[num] > 1:
                log_drop({'number': num, 'page': line.page, 'text': line.text}, 'id_collision', f'renamed {num}~{used_ids[num]}')
                num += '~' + str(used_ids[num])
            title = h['title']
            own_body = []
            if (len(title.split()) > 15 or re.search(r'\b(shall|should|must|means)\b', title, re.I) or
                    len(title.split()) >= 5 and re.search(r'[.!]$', title)):
                title = parent['title'] if parent else ''
                own_body.append(line)
            if not title and i + 1 < len(lines):
                following = lines[i + 1]
                if (following.page == line.page and following.role == 'continuation' and
                        (following.bold or h['kind'] in ('article', 'annex')) and
                        not any(re.match(lv['pattern'], following.text) for lv in config['levels'])):
                    title = following.text
                    following.role = 'title'
            if not title and parent:
                title = parent['title']
            skip = bool(parent and parent['_skip']) or any(re.search(p, title, re.I) for p in config.get('skip_sections', []))
            current = dict(id=f'{doc}:{num}', doc=doc, number=num, title=title, text='',
                           parent=parent['id'] if parent else None, source_pdf=Path(pdf).name, page=line.page,
                           informative=bool(parent and parent['informative']) or '(informative)' in line.text.casefold(),
                           _depth=depth, _kind=h['kind'], _body=own_body, _skip=skip, _x0=line.bbox[0])
            candidates.append(current)
            stack.append(current)
            line.role, line.number, line.parent_number = 'clause_start', num, parent['number'] if parent else ''
        elif line.role != 'title':
            if current:
                current['_body'].append(line)
                line.number, line.parent_number = current['number'], current['parent'].split(':', 1)[1] if current['parent'] else ''
            else:
                outside.append(line)
                line.role = 'noise'
        previous = line.text
    flush_outside()
    kept, seen_text, all_candidates = [], set(), {c['id']: c for c in candidates}
    for c in candidates:
        if c['_kind'] != 'table':
            # Preserve paragraphs across PDF blocks; NOTE/EXAMPLE remains a separate line.
            body = []
            prev_block = None
            for l in c['_body']:
                block = (l.page, l.block)
                if prev_block is not None and block != prev_block and body and not body[-1].text.endswith('-'):
                    body.append(Line('\u2029', l.page))
                body.append(l)
                prev_block = block
            c['text'] = join_text(body, vocabulary, stats)
        fingerprint = re.sub(r'\s+', ' ', c['text']).casefold()
        reason = ('skipped_section' if c['_skip'] else 'header_only' if not c['text'] else
                  'too_short' if len(c['text']) < 25 and not c['title'] else
                  'duplicate' if fingerprint in seen_text else
                  'toc_fragment' if sum(toc_like(l.text, config) for l in c['_body']) > max(0, len(c['_body']) / 2) else None)
        if reason:
            log_drop(c, reason)
            continue
        seen_text.add(fingerprint)
        c['informative'] |= bool(c['text'] and all(re.match(r'^(NOTE|EXAMPLE)\b', p) for p in c['text'].splitlines()))
        if len(c['text']) > 6000:
            log_drop(c, 'long_clause', 'warning: retained')
        kept.append(c)
    kept_ids = {c['id'] for c in kept}
    for c in kept:
        while c['parent'] and c['parent'] not in kept_ids:
            c['parent'] = all_candidates[c['parent']]['parent']
    stats['clauses_per_level'] = dict(Counter(str(c['_depth']) for c in kept))
    result = [{k: v for k, v in c.items() if not k.startswith('_')} for c in kept]
    stats['kept_clauses'] = len(result)
    stats['drops'] = dict(Counter(d['reason'] for d in drops))
    stats['warnings'] = dict(Counter(w['code'] for w in warnings))
    stats['bold_calibration'] = {k: {'candidates': v[0], 'bold': v[1], 'share': v[1] / v[0],
                                   'require_bold': next(lv.get('require_bold', False) for lv in config['levels'] if lv['name'] == k)}
                                 for k, v in calibration.items()}
    stats['bookmark_matches'] = sum(any(difflib.SequenceMatcher(None, b[1].casefold(), c['title'].casefold()).ratio() >= .8 for c in result) for b in bookmarks)
    stats['quality_report'] = quality_report(result, raw_text, stripped_chars, pages)
    exported = [dict(line_id=f'{doc}:{l.line_id}', doc=doc, page=l.page, text=original[l.line_id],
                     pred_role='noise' if l.role in ('source_note', 'title') else l.role,
                     pred_number=l.number, pred_parent_number=l.parent_number) for l in lines]
    return dict(clauses=result, stats=stats, drops=drops, warnings=warnings, lines=exported,
                ocr=({'doc': doc, 'file': str(pdf), 'pages': pages,
                      'text_pages': len({l.page for l in lines})} if len({l.page for l in lines}) < .1 * pages else None))


def json_write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def csv_write(path, fields, rows):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def cache_key(pdf, config, slices=None):
    return hashlib.sha256(Path(pdf).read_bytes()).hexdigest() + hashlib.sha256(
        json.dumps([config, slices], sort_keys=True).encode()).hexdigest() + PARSER_VERSION


def run(root=ROOT, doc=None, force=False, include_optional=False, pdf=None, as_code=None):
    root = Path(root)
    config = yaml.safe_load((root / 'ingest/sources.yaml').read_text(encoding='utf-8'))
    manifest = json.loads((root / 'data/corpus_manifest.json').read_text(encoding='utf-8'))
    slices = yaml.safe_load((root / 'corpus/eu_mdr_slices.yaml').read_text(encoding='utf-8'))
    results, skipped = {}, []
    if pdf:
        if not as_code:
            raise ValueError('--pdf requires --as')
        path = Path(pdf)
        fallback = config.get('who-decon', {'mode': 'decimal', 'levels': [], 'max_depth': 2})
        result = parse_document(path, as_code, config.get(as_code, fallback))
        ocr_path = root / 'data/ocr_needed.json'
        ocr = json.loads(ocr_path.read_text()) if ocr_path.exists() else []
        ocr = [r for r in ocr if r['doc'] != as_code]
        if result['ocr']:
            ocr.append(result['ocr'])
            json_write(ocr_path, ocr)
            print(f'OCR needed: {as_code}; zero clauses; clauses.json unchanged')
            return {as_code: result}, []
        validate(result['clauses'], {as_code: {}})
        # Ad-hoc documents never overwrite the corpus contract file.
        json_write(root / f'data/cache/{as_code}.json', result)
        return {as_code: result}, []
    selected = [code for code, meta in sorted(manifest.items())
                if meta['status'] in ('ok', 'ok_manual', 'ok_manual_html_print') and
                (config[code]['enabled'] or include_optional) and (not doc or code == doc)]
    if doc and not selected:
        raise ValueError(f'document not enabled or not in manifest: {doc}')
    for code in selected:
        path = root / f'data/pdfs/{code}.pdf'
        marker = slices if code == 'eu-mdr' else None
        key = cache_key(path, config[code], marker)
        cache = root / f'data/cache/{code}.json'
        result = json.loads(cache.read_text(encoding='utf-8')) if cache.exists() else None
        if not force and result and result.get('key') == key:
            skipped.append(code)
            print(f'SKIP {code}: cache unchanged')
        else:
            result = parse_document(path, code, config[code], marker)
            result['key'] = key
            json_write(cache, result)
        results[code] = result
    # A --doc refresh must preserve other already-ingested documents.
    output = root / 'data/clauses.json'
    old = json.loads(output.read_text(encoding='utf-8')) if doc and output.exists() else []
    clauses = [c for c in old if c['doc'] not in selected]
    clauses.extend(c for code in selected for c in results[code]['clauses'])
    clauses.sort(key=lambda c: c['doc'])
    validate(clauses, manifest)
    json_write(output, clauses)
    # Merge diagnostics on a one-document refresh too.
    all_results = dict(results)
    if doc:
        for code in sorted({c['doc'] for c in old} - set(selected)):
            cached = root / f'data/cache/{code}.json'
            if cached.exists():
                all_results[code] = json.loads(cached.read_text(encoding='utf-8'))
    all_results = dict(sorted(all_results.items()))
    json_write(root / 'data/ingest_stats.json', {k: r['stats'] for k, r in all_results.items()})
    ocr_path = root / 'data/ocr_needed.json'
    previous_ocr = json.loads(ocr_path.read_text()) if ocr_path.exists() else []
    json_write(ocr_path, [r for r in previous_ocr if r['doc'] not in selected] +
               [r['ocr'] for r in results.values() if r['ocr']])
    csv_write(root / 'data/drop_log.csv', DROP_FIELDS, [d for r in all_results.values() for d in r['drops']])
    csv_write(root / 'data/parse_warnings.csv', WARNING_FIELDS, [w for r in all_results.values() for w in r['warnings']])
    print('doc | pages | raw headings | kept | drops | expected')
    for code, r in results.items():
        s = r['stats']
        band = s['expected_clauses']
        warn = ' WARN' if band and not band[0] <= s['kept_clauses'] <= band[1] else ''
        print(f"{code} | {s['pages']} | {s['raw_headings']} | {s['kept_clauses']} | {s['drops']} | {band}{warn}")
        for level, b in s['bold_calibration'].items():
            if b['require_bold'] and b['share'] < .8:
                print(f"WARN {code}/{level}: bold {b['share']:.1%}; requirement retained")
        print('quality', code, json.dumps(s['quality_report'], ensure_ascii=True))
    print('TOTAL', len(clauses), 'contract valid')
    return results, skipped


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument('--doc')
    p.add_argument('--force', action='store_true')
    p.add_argument('--include-optional', action='store_true')
    p.add_argument('--pdf')
    p.add_argument('--as', dest='as_code')
    args = p.parse_args(argv)
    if bool(args.pdf) != bool(args.as_code):
        p.error('--pdf and --as must be used together')
    run(**vars(args))


if __name__ == '__main__':
    main()
