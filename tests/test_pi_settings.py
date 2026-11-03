"""R22 P2-P4 + D2: PageIndex settings, model routes and the Azure backend, offline only."""
import importlib
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'test-key-not-real'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for key in ('AZURE_OPENAI_ENDPOINT', 'AZURE_OPENAI_API_KEY', 'OPENAI_BASE_URL'):
        monkeypatch.delenv(key, raising=False)

    def deny(*args, **kwargs):
        raise AssertionError('Network forbidden')
    monkeypatch.setattr(socket.socket, 'connect', deny)


@pytest.fixture
def pc(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'pageindex'))
    return importlib.import_module('pi_common')


def azure_env(monkeypatch):
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://res.openai.azure.com/')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', SECRET)


def test_repo_settings_are_standard_anthropic_and_have_no_judge(pc):
    s = pc.load_settings()
    assert s.index_mode == 'standard'
    assert s.index_model.startswith('anthropic/') and s.summary_model.startswith('anthropic/')
    assert s.judge_model is None and s.judge_repeats == 3
    assert s.embedding_model == 'text-embedding-3-small'
    assert s.allow_unpinned_pageindex is False      # D1: unpinned PageIndex refused by default


def test_unknown_index_mode_is_refused(pc):
    with pytest.raises(ValueError):
        pc.Settings(index_mode='auto')


@pytest.mark.parametrize('name', ['gpt-5-mini', 'gpt-4.1-mini', 'o4-mini', 'text-embedding-3-small'])
def test_known_deployment_names_pass(pc, name):
    pc.check_deployment_names({'role': name})


@pytest.mark.parametrize('name', ['gpt-5-mini-prod', 'my-judge', 'gpt-4o-mini', 'GPT-5-MINI', '',
                                  'claude-haiku-4-5', 'openai/gpt-5-mini'])
def test_unknown_deployment_names_are_refused(pc, name):
    with pytest.raises(ValueError, match='Unknown Azure deployment'):
        pc.check_deployment_names({'judge': name})


@pytest.mark.parametrize('name,expected', [('gpt-5-mini', True), ('o4-mini', True),
                                           ('gpt-4.1-mini', False), ('text-embedding-3-small', False)])
def test_reasoning_detection_by_name(pc, name, expected):
    assert pc.is_reasoning_model(name) is expected


def test_azure_v1_url(pc):
    assert pc.azure_v1_base_url('https://res.openai.azure.com/') == 'https://res.openai.azure.com/openai/v1/'
    assert pc.azure_v1_base_url('https://res.openai.azure.com') == 'https://res.openai.azure.com/openai/v1/'


def test_anthropic_route_keeps_max_tokens_and_needs_no_azure(pc):
    assert pc.index_backend(pc.Settings()) == {'max_tokens': 16000}
    assert pc.index_backend(pc.Settings(litellm_max_tokens=None)) == {}


def test_azure_route_passes_api_base_and_key_and_never_caps(pc, monkeypatch):
    azure_env(monkeypatch)
    s = pc.Settings(index_model='openai/gpt-5-mini', summary_model='openai/gpt-5-mini',
                    litellm_max_tokens=None)
    backend = pc.index_backend(s)
    assert backend == {'api_base': 'https://res.openai.azure.com/openai/v1/', 'api_key': SECRET}
    assert 'max_tokens' not in backend and 'max_completion_tokens' not in backend


def test_azure_route_refuses_a_max_tokens_cap(pc, monkeypatch):
    azure_env(monkeypatch)
    s = pc.Settings(index_model='openai/gpt-5-mini', summary_model='openai/gpt-5-mini')
    with pytest.raises(ValueError, match='litellm_max_tokens: null'):
        pc.index_backend(s)


def test_azure_route_refuses_unknown_deployment(pc, monkeypatch):
    azure_env(monkeypatch)
    s = pc.Settings(index_model='openai/gpt-5-mini', summary_model='openai/summary-dep',
                    litellm_max_tokens=None)
    with pytest.raises(ValueError, match='summary-dep'):
        pc.index_backend(s)


def test_azure_route_needs_env_credentials(pc):
    s = pc.Settings(index_model='openai/gpt-5-mini', summary_model='openai/gpt-5-mini',
                    litellm_max_tokens=None)
    with pytest.raises(RuntimeError, match='AZURE_OPENAI_ENDPOINT'):
        pc.index_backend(s)


def test_mixed_routes_are_refused(pc, monkeypatch):
    azure_env(monkeypatch)
    s = pc.Settings(index_model='openai/gpt-5-mini', summary_model='anthropic/claude-sonnet-5-5',
                    litellm_max_tokens=None)
    with pytest.raises(ValueError, match='same route'):
        pc.index_backend(s)


@pytest.mark.parametrize('model', ['gpt-5-mini', 'gpt-6-luna', 'azure/gpt-5-mini'])
def test_bare_or_other_provider_names_are_refused(pc, model):
    with pytest.raises(ValueError, match='must start with'):
        pc.index_backend(pc.Settings(index_model=model, summary_model=model))


@pytest.mark.parametrize('field', ['index_model', 'summary_model'])
def test_models_must_be_explicit(pc, field):
    with pytest.raises(ValueError, match=field):
        pc.index_backend(pc.Settings(**{field: ' '}))


def test_openai_base_url_warns_and_secret_is_not_printed(pc, monkeypatch, capsys):
    azure_env(monkeypatch)
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://elsewhere.example/v1')
    s = pc.Settings(index_model='openai/gpt-5-mini', summary_model='openai/gpt-5-mini',
                    litellm_max_tokens=None)
    pc.index_backend(s)
    err = capsys.readouterr()
    assert 'OPENAI_BASE_URL' in err.err
    assert SECRET not in err.err + err.out


def test_old_manifest_records_still_load(pc):
    rec = pc.DocRecord.model_validate(dict(
        doc_id='gtp@2019', family='gtp', title='t', version='2019', sha256='0' * 64,
        pdf_path='p.pdf', tree_path='t.json', page_count=3, index_mode='standard',
        index_model='anthropic/x', pageindex_version='0.2.10', built_at='2026-10-01T00:00:00Z'))
    assert rec.pageindex_commit == 'unknown' and rec.index_route == '' and rec.summary_model == ''


def test_installed_commit_reads_pip_direct_url(pc, monkeypatch):
    from importlib import metadata

    class Dist:
        def read_text(self, name):
            assert name == 'direct_url.json'
            return '{"url": "https://github.com/VectifyAI/PageIndex", "vcs_info": {"vcs": "git", "commit_id": "%s"}}' % pc.PAGEINDEX_PIN_SHA
    monkeypatch.setattr(metadata, 'distribution', lambda name: Dist())
    assert pc.installed_pageindex_commit() == pc.PAGEINDEX_PIN_SHA

    def missing(name):
        raise metadata.PackageNotFoundError(name)
    monkeypatch.setattr(metadata, 'distribution', missing)
    assert pc.installed_pageindex_commit().startswith('unknown')
