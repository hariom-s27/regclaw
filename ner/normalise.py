"""Deterministic, type-scoped aliases; similarity produces review proposals only."""
import csv
import difflib
from pathlib import Path
import re
import unicodedata

TYPES = {'biological_effects': 'BiologicalEffect', 'chemicals': 'Chemical',
         'device_types': 'DeviceType', 'regulatory_actors': 'RegulatoryActor',
         'lifecycle_phases': 'LifecyclePhase', 'keywords': 'keyword'}
SPELLINGS = {'sensitisation': 'sensitization', 'haemocompatibility': 'hemocompatibility',
             'sterilisation': 'sterilization', 'vaporised': 'vaporized'}
SINGULARS = {'devices': 'device', 'catheters': 'catheter', 'implants': 'implant', 'stents': 'stent',
             'phases': 'phase', 'procedures': 'procedure'}


def surface(text):
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'[‐‑‒–—−]', '-', text)
    return re.sub(r'\s+', ' ', text).casefold().strip(' \t\n\"\'.,;:')


def local_abbreviations(text):
    """A small right-to-left alignment recognizer for long form (ABBR)."""
    definitions = {}
    for m in re.finditer(r'([\w ,\-]{3,100})\s*\(([A-Z][A-Z0-9&-]{1,9})\)', text):
        long, short = m[1].strip(), m[2]
        letters = [c.casefold() for c in short if c.isalnum()]
        i = len(long) - 1
        valid = True
        for j in range(len(letters)-1, -1, -1):
            while i >= 0 and (long[i].casefold() != letters[j] or
                              (j == 0 and i > 0 and long[i-1].isalnum())):
                i -= 1
            if i < 0:
                valid = False; break
            if j:
                i -= 1
        if valid:
            definitions[surface(short)] = surface(long[i:])
    return definitions


class Normaliser:
    def __init__(self, aliases=None):
        path = Path(aliases) if aliases else Path(__file__).with_name('aliases.csv')
        with path.open(encoding='utf-8', newline='') as f:
            self.rows = list(csv.DictReader(f))
        self.lookup = {}
        for row in self.rows:
            for alias in row['alias'].split(' / '):
                self.lookup[(row['type'], surface(alias))] = row

    def alias(self, mention, kind, safe_only=True):
        row = self.lookup.get((TYPES.get(kind, kind), surface(mention)))
        return row if row and (not safe_only or row['safe_global'] == '1') else None

    def canonical(self, mention, kind, clause='', enum_canonical=None):
        candidate = surface(mention)
        local = local_abbreviations(clause)
        candidate = local.get(candidate, candidate)
        for source, target in SPELLINGS.items():
            candidate = re.sub(r'\b' + source + r'\b', target, candidate)
        if kind in ('device_types', 'lifecycle_phases', 'DeviceType', 'LifecyclePhase'):
            candidate = ' '.join(SINGULARS.get(word, word) for word in candidate.split())
        resolved = False
        visited = set()
        while candidate not in visited:
            visited.add(candidate)
            row = self.alias(candidate, kind)
            if not row:
                break
            resolved = True
            candidate = surface(row['canonical_name'])
        if not resolved and enum_canonical is not None:
            candidate = surface(enum_canonical)
        return candidate.replace('_', ' '), resolved

    def legitimate(self, mention, canonical, kind, clause):
        expected = surface(canonical).replace('_', ' ')
        if expected == surface(mention).replace('_', ' '):
            return True
        resolved, alias = self.canonical(mention, kind, clause)
        return expected == resolved and (alias or surface(mention) in local_abbreviations(clause) or
                                        surface(mention) in SPELLINGS or
                                        kind == 'device_types' and resolved != surface(mention))


def review_candidates(candidate, existing, kind):
    proposals = []
    for name in sorted(existing):
        ratio = difflib.SequenceMatcher(None, candidate, name).ratio()
        if candidate != name and ratio >= .90:
            proposals.append({'type': kind, 'candidate': candidate, 'existing': name,
                              'ratio': round(ratio, 6), 'accept': ''})
    return proposals
