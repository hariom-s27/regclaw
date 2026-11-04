"""Ruleset identity for every S10 output + a content lock on the rule YAMLs.

`ruleset` and `disclaimer` are looked up from the code path the engine actually used
(profile.framework, plus whether the 2025 path fell back to FDA rows); an unknown
framework raises. RULESET_FILES_SHA256 stores the sha256 of the four rule-data YAMLs
next to RULESET_VERSION: any byte change in them must come with a version bump and new
shas (enforced by tests/test_s10_ruleset.py). ENGINE_FILES_SHA256 does the same for the
decision/wording CODE (engine, guard, over-claim lint), tied to standards.ENGINE_VERSION.
Hashes use CRLF->LF normalised bytes so a Windows autocrlf checkout gives the same value.
"""
import hashlib
from pathlib import Path

from . import ENGINE_VERSION

RULESET_VERSION = 'S10-ruleset.v1'
RULESET_FILES_SHA256 = {
    'fda2023_attachment_a.v1.yaml': '5989b50fd98a89c6d4dad246bf88268b2d009eb98cdb00ec4d750a8825b44804',
    'iso2025_anchor_rows.v1.yaml': 'b74e9fcece0769207fcd5125de54d27e3d3c5559cbed9a9ff624611e217271f0',
    'endpoint_to_part.v1.yaml': '7116f8f427f0b093cd7c3231ad674318020f81d1d851294a9b7a61c5802cb56f',
    'labels.v1.yaml': '0a22730db5c566f44248ebdb6e3e6c46f6177facd1d3deee064023a898a22d26',
}
MATRIX_FILE = 'fda2023_attachment_a.v1.yaml'

FDA_RULESET = 'FDA-2023-AttA (ISO 10993-1:2018 basis)'
FDA_DISCLAIMER = ("Based on FDA's 2023 biocompatibility guidance; ISO 10993-1:2025 is partially "
                  "recognised by FDA (May 2026) and its tables differ. Not reviewed against ISO 10993-1:2025.")
ISO2025_RULESET = 'ISO_10993_1_2025 anchor rows (PMDA examples via R9) - not FDA Attachment A'
ISO2025_DISCLAIMER = ("Not FDA's 2023 Attachment A matrix: labels come from ISO 10993-1:2025 anchor rows "
                      "(PMDA examples via R9, not verified by a human); other 2025 rows are unverified. "
                      "Not reviewed as a complete ISO 10993-1:2025 evaluation.")
ISO2025_MIXED_RULESET = ('ISO_10993_1_2025 anchor rows (PMDA examples via R9) with FDA-2023-AttA fallback '
                         'for unverified rows - mixed, not FDA Attachment A alone')
ISO2025_MIXED_DISCLAIMER = ("Not FDA's 2023 Attachment A matrix alone: ISO 10993-1:2025 anchor rows (PMDA examples "
                            "via R9, not verified by a human) were used where a row exists and FDA 2023 Attachment A "
                            "rows elsewhere (see assumptions). "
                            "Not reviewed as a complete ISO 10993-1:2025 evaluation.")
ISO2018_RULESET = 'FDA-2023-AttA (ISO 10993-1:2018 basis) - applied for requested framework ISO_10993_1_2018'


# Code lock: these shas belong to ENGINE_FILES_VERSION, which must equal standards.ENGINE_VERSION.
ENGINE_FILES_VERSION = 'S10.v1'
ENGINE_FILES_SHA256 = {
    'engine.py': 'd83bdf0bd99db20e4c7b7c3c28bff08f650e985c7d58129e036453275438bc0b',
    'guard.py': '27423a00b50ccb7e7f7c46dd099cbea9611b2b28e133ceac969af61eaa207e13',
    'overclaim.py': 'e14c4c1eb3b2f676000adb44f5cf68ae72e893cd2b1e6e17863bc71e6f1ba26f',
}


class RulesetChanged(AssertionError):
    """Rule-data bytes differ from the shas stored with RULESET_VERSION."""


class EngineChanged(AssertionError):
    """Engine/guard/lint code differs from the shas stored with ENGINE_VERSION."""


def normalised_sha(raw: bytes) -> str:
    return hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest()


def combined_sha(file_shas: dict) -> str:
    return hashlib.sha256(''.join(f'{n} {file_shas[n]}\n' for n in sorted(file_shas)).encode()).hexdigest()


RULESET_SHA256 = combined_sha(RULESET_FILES_SHA256)
ENGINE_SHA256 = combined_sha(ENGINE_FILES_SHA256)


def file_shas(folder=None) -> dict:
    folder = Path(folder) if folder else Path(__file__).with_name('data')
    return {name: normalised_sha((folder / name).read_bytes()) for name in RULESET_FILES_SHA256}


def check_ruleset(folder=None) -> str:
    """Return RULESET_SHA256, or raise RulesetChanged naming the changed files."""
    actual = file_shas(folder)
    changed = sorted(n for n in RULESET_FILES_SHA256 if actual[n] != RULESET_FILES_SHA256[n])
    if changed:
        raise RulesetChanged(f'Rule data changed without a ruleset version bump ({RULESET_VERSION}): {changed}. '
                             'Bump RULESET_VERSION and update RULESET_FILES_SHA256 in standards/ruleset.py.')
    return RULESET_SHA256


def engine_file_shas(folder=None) -> dict:
    folder = Path(folder) if folder else Path(__file__).parent
    return {name: normalised_sha((folder / name).read_bytes()) for name in ENGINE_FILES_SHA256}


def check_engine(folder=None, version=None) -> str:
    """Return ENGINE_SHA256, or raise EngineChanged if the code changed or ENGINE_VERSION
    was bumped without re-recording the shas (both must move together)."""
    version = ENGINE_VERSION if version is None else version
    if version != ENGINE_FILES_VERSION:
        raise EngineChanged(f'ENGINE_VERSION {version!r} != ENGINE_FILES_VERSION {ENGINE_FILES_VERSION!r}: '
                            're-record ENGINE_FILES_SHA256 in standards/ruleset.py for the new version.')
    actual = engine_file_shas(folder)
    changed = sorted(n for n in ENGINE_FILES_SHA256 if actual[n] != ENGINE_FILES_SHA256[n])
    if changed:
        raise EngineChanged(f'Engine code changed without an ENGINE_VERSION bump ({ENGINE_VERSION}): {changed}. '
                            'Bump standards.ENGINE_VERSION, then ENGINE_FILES_VERSION + ENGINE_FILES_SHA256 here.')
    return ENGINE_SHA256


def ruleset_for(framework: str, fallback_used: bool = False) -> tuple[str, str]:
    """(ruleset, disclaimer) for the code path the engine used; unknown framework raises.

    `fallback_used` = rules['framework_row_unverified']; it only changes the 2025 path
    (where it means some component fell back to FDA rows). On the FDA paths it marks a
    contact combination absent from the matrix, which still uses FDA rows.
    """
    if framework == 'FDA_2023_ATTACHMENT_A':
        return FDA_RULESET, FDA_DISCLAIMER
    if framework == 'ISO_10993_1_2018':
        # engine.rule_03 uses the FDA matrix for every non-2025 framework.
        return ISO2018_RULESET, FDA_DISCLAIMER
    if framework == 'ISO_10993_1_2025':
        return (ISO2025_MIXED_RULESET, ISO2025_MIXED_DISCLAIMER) if fallback_used else (ISO2025_RULESET, ISO2025_DISCLAIMER)
    raise ValueError(f'Unknown framework {framework!r}: no ruleset/disclaimer defined')
