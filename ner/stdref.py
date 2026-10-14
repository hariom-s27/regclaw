import re, unicodedata
def norm(t):
    t = unicodedata.normalize("NFKC", t)
    t = re.sub(r"[‐‑‒–—−]", "-", t)   # dash variants
    t = re.sub(r"-\s*\n\s*", "-", t)                                  # line-break hyphenation
    return re.sub(r"[ \t ]+", " ", t)

YEAR = r"(?:19|20)\d{2}"
ISO_RE = re.compile(rf"""
 (?<![A-Za-z0-9])
 (?P<adopt>(?:(?:BS|DIN|NF|SS|I\.S\.|EVS|UNE|UNI|IS)\s+)?EN\s+)?
 (?P<org>ISO/IEC/IEEE|ISO/IEC|ISO|IEC)
 (?:\s*/\s*(?P<dt1>TS|TR|PAS)|\s+(?P<dt2>TS|TR|PAS))?
 \s+(?P<num>\d{{3,5}})
 (?:-(?P<part>\d{{1,4}}(?:-\d{{1,3}})?))?
 (?::(?P<year>{YEAR}))?
 (?P<mods>(?:\s*/\s*(?:Amd|AMD|Cor|COR|AC|A)\s*\d+(?::{YEAR})?)*)
 (?P<enmods>(?:\s*\+\s*(?:A|AC)\d+:{YEAR})*)
""", re.X)

ASTM_RE = re.compile(rf"""
 \bASTM\s+(?:International\s+)?
 (?P<letter>[A-G])\s?(?P<num>\d{{1,5}})(?:/[A-G]\d{{1,5}}M)?
 (?:-(?P<year>\d{{2}}(?:\d{{2}})?)(?P<rev>[a-z])?)?
 (?:\s?\((?P<reapp>{YEAR})\))?
""", re.X)

USP_RE = re.compile(r"""
 \bUSP(?:\s*-\s*NF|\s+\d{2}\s*[-–,]?\s*NF\s*\d{2},?)?\s*
 [<⟨〈〈‹]\s*(?P<chap>\d{1,4})\s*[>⟩〉〉›]
""", re.X)


def references(text):
    """Canonical graph ids plus source matches; editions never enter the id."""
    text = norm(text)
    found = []
    for pattern in (ISO_RE, ASTM_RE, USP_RE):
        for match in pattern.finditer(text):
            g = match.groupdict()
            if pattern is ISO_RE:
                org = g['org']
                dt = g['dt1'] or g['dt2']
                base = org + ('/' + dt if dt else '') + ' ' + g['num']
                canonical = base + ('-' + g['part'] if g['part'] else '')
            elif pattern is ASTM_RE:
                canonical = 'ASTM ' + g['letter'] + g['num']
            else:
                canonical = 'USP <' + g['chap'] + '>'
            found.append((match.start(), canonical, match.group(0).strip()))
            if pattern is ISO_RE and g['part']:
                tail = re.match(r'(?:\s*(?:,|and|or)\s*-\d+(?:-\d+)*)+', text[match.end():])
                if tail:
                    for m in re.finditer(r'-(\d+(?:-\d+)*)', tail[0]):
                        found.append((match.end() + m.start(), base + '-' + m[1], m[0]))
    for match in re.finditer(r'\bParts?\s+([\d,\s]+(?:and\s+\d+)?)\s+of\s+(ISO(?:/IEC)?\s+\d{3,5})\b', text, re.I):
        base = match[2]
        found = [f for f in found if not (match.start() <= f[0] < match.end() and f[1] == base)]
        for number in re.findall(r'\d+', match[1]):
            found.append((match.start(), base + '-' + number, match[0]))
    ids, raw = [], []
    for _, canonical, surface in sorted(found, key=lambda x: x[0]):
        if canonical not in ids:
            ids.append(canonical)
        if surface not in raw:
            raw.append(surface)
    return ids, raw


def is_standard(text):
    return any(r.search(norm(text)) for r in (ISO_RE, ASTM_RE, USP_RE))
