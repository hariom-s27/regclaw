"""S10b-04 practice-profile format, on FAKE profiles only (no real files)."""
import copy

import pytest

from eval.s10.formats import (PRACTICE_FORMAT, FormatError, validate_practice_set, validate_profile_set)


def entry(i=1, **over):
    e = dict(id=f'FAKE-PR-{i}', split='practice', contact_category='surface/intact_skin', duration='A',
             flags=['attachment_g_material'],
             profile=dict(device_name=f'Zorblat gadget {i}', components=[dict(
                 name='FAKE part', contact_nature='surface', contact_site='intact_skin',
                 exposure=dict(pattern='single', hours_per_use=2),
                 materials=[dict(name='FAKE polymer', material_class='polymer')], sterilization='non_sterile')]))
    e.update(over)
    return e


def doc(*entries):
    return dict(format=PRACTICE_FORMAT, split='practice', profiles=list(entries) or [entry(1), entry(2)])


def test_valid_practice_set():
    assert sorted(validate_practice_set(doc())) == ['FAKE-PR-1', 'FAKE-PR-2']


@pytest.mark.parametrize('extra', [dict(stratum='S1'), dict(comment='made-up note')])
def test_stratum_and_comment_rejected(extra):
    with pytest.raises(FormatError):
        validate_practice_set(doc(entry(1, **extra)))


def test_wrong_split_rejected():
    with pytest.raises(FormatError):
        validate_practice_set(doc(entry(1, split='dev')))
    d = doc()
    d['split'] = 'dev'
    with pytest.raises(FormatError):
        validate_practice_set(d)


def test_duplicate_id_rejected():
    with pytest.raises(FormatError, match='duplicate'):
        validate_practice_set(doc(entry(1), entry(1)))


def test_wrong_duration_and_flags_rejected():
    with pytest.raises(FormatError, match='duration'):
        validate_practice_set(doc(entry(1, duration='C')))
    with pytest.raises(FormatError, match='flags'):
        validate_practice_set(doc(entry(1, flags=[])))


def test_eval_profile_set_still_rejects_practice_split():
    e = entry(1, stratum='S1')
    d = dict(format='s10-eval-profiles.v1', split='practice', profiles=[e])
    with pytest.raises(FormatError):
        validate_profile_set(d)
    d2 = copy.deepcopy(d)
    d2['split'] = 'dev'
    with pytest.raises(FormatError):
        validate_profile_set(d2)  # entry split "practice" still refused
