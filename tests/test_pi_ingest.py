"""R22 P1/P2/P4: PageIndex ingest wiring against FAKE pageindex modules (no install, no network)."""
import contextvars
import importlib
import json
import socket
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace as NS

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
def fake_pageindex(monkeypatch):
    """Minimal stand-ins for pageindex.utils / page_index_classic / flash at 50a71b6."""
    calls = []
    backend = contextvars.ContextVar('pageindex_llm_backend', default=None)

    class ConfigLoader:
        def load(self, user_opt):
            return NS(**user_opt)

    def page_index_main(doc, opt=None):
        calls.append(('standard', doc, vars(opt), backend.get()))
        return {'doc_name': 'x.pdf', 'doc_description': 'desc',
                'structure': [{'title': 'A', 'node_id': '0000', 'start_index': 1, 'end_index': 2}]}

    def page_index_flash(pdf, **kw):
        calls.append(('flash', pdf, kw, backend.get()))
        return {'structure': []}

    mods = {'pageindex': ModuleType('pageindex'), 'pageindex.utils': ModuleType('pageindex.utils'),
            'pageindex.page_index_classic': ModuleType('pageindex.page_index_classic'),
            'pageindex.flash': ModuleType('pageindex.flash'),
            'pageindex.flash.api': ModuleType('pageindex.flash.api')}
    mods['pageindex.utils'].ConfigLoader = ConfigLoader
    mods['pageindex.utils']._llm_backend = backend
    mods['pageindex.page_index_classic'].page_index_main = page_index_main
    mods['pageindex.flash'].page_index_flash = page_index_flash
    mods['pageindex.flash.api'].flash_rejection_reason = lambda result, standard_hint='': None
    for name, mod in mods.items():
        monkeypatch.setitem(sys.modules, name, mod)
    return NS(calls=calls, backend=backend)


@pytest.fixture
def pi(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'pageindex'))
    ingest = importlib.import_module('pi_ingest')
    common = importlib.import_module('pi_common')
    monkeypatch.setattr(ingest, 'text_layer_report', lambda pdf: {'pages': 2, 'empty_pages': 0, 'scanned': False})
    monkeypatch.setattr(ingest, 'page_count', lambda pdf: 2)
    return NS(ingest=ingest, common=common)


def azure_settings(pi, tmp_path, **kw):
    return pi.common.Settings(store_dir=tmp_path / 'store', index_model='openai/gpt-5-mini',
                              summary_model='openai/gpt-5-mini', litellm_max_tokens=None, **kw)


def test_default_standard_calls_page_index_main_with_explicit_models(pi, fake_pageindex, tmp_path):
    s = pi.common.Settings(store_dir=tmp_path / 'store')
    pi.ingest.build_tree('doc.pdf', s)
    (mode, doc, opt, backend), = fake_pageindex.calls
    assert mode == 'standard' and doc == 'doc.pdf'
    assert opt['index_model'] == s.index_model and opt['summary_model'] == s.summary_model
    assert backend == {'max_tokens': 16000}          # anthropic route keeps its cap
    assert fake_pageindex.backend.get() is None      # contextvar reset after the build


def test_azure_route_sends_base_and_key_but_no_cap(pi, fake_pageindex, tmp_path, monkeypatch):
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://res.openai.azure.com')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', SECRET)
    pi.ingest.build_tree('doc.pdf', azure_settings(pi, tmp_path))
    (_, _, opt, backend), = fake_pageindex.calls
    assert backend == {'api_base': 'https://res.openai.azure.com/openai/v1/', 'api_key': SECRET}
    assert opt['index_model'] == 'openai/gpt-5-mini' and opt['summary_model'] == 'openai/gpt-5-mini'


def test_flash_only_when_explicit(pi, fake_pageindex, tmp_path):
    s = pi.common.Settings(store_dir=tmp_path / 'store', index_mode='flash')
    pi.ingest.build_tree('doc.pdf', s)
    assert fake_pageindex.calls[0][0] == 'flash'
    assert fake_pageindex.calls[0][2]['summary_model'] == s.summary_model


def test_bad_settings_fail_before_any_pageindex_call(pi, fake_pageindex, tmp_path):
    with pytest.raises(ValueError):
        pi.ingest.build_tree('doc.pdf', pi.common.Settings(store_dir=tmp_path, index_model=''))
    with pytest.raises(RuntimeError):   # Azure route without env credentials
        pi.ingest.build_tree('doc.pdf', azure_settings(pi, tmp_path))
    assert fake_pageindex.calls == []


def test_ingest_records_mode_models_route_and_commit_without_secret(pi, fake_pageindex, tmp_path, monkeypatch):
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://res.openai.azure.com')
    monkeypatch.setenv('AZURE_OPENAI_API_KEY', SECRET)
    monkeypatch.setattr(pi.ingest, 'installed_pageindex_commit', lambda: pi.common.PAGEINDEX_PIN_SHA)
    pdf = tmp_path / 'g.pdf'
    pdf.write_bytes(b'%PDF-1.4 fake bytes')
    s = azure_settings(pi, tmp_path)
    rec = pi.ingest.ingest(str(pdf), 'gtp', '2019', s=s)
    assert (rec.index_mode, rec.index_model, rec.summary_model) == ('standard', 'openai/gpt-5-mini', 'openai/gpt-5-mini')
    assert rec.index_route == 'azure_openai' and rec.pageindex_commit == pi.common.PAGEINDEX_PIN_SHA
    manifest = (tmp_path / 'store' / 'manifest.json').read_text()
    assert SECRET not in manifest and 'api_key' not in manifest
    assert json.loads(manifest)['documents']['gtp@2019']['index_mode'] == 'standard'


def _pdf(tmp_path, data=b'%PDF-1.4 other bytes'):
    pdf = tmp_path / 'g.pdf'
    pdf.write_bytes(data)
    return str(pdf)


OTHER = '646deeb650005885f44001471d15ab9eab1bbc13'


@pytest.mark.parametrize('installed', [OTHER, 'unknown (not installed)'])
def test_unpinned_pageindex_is_refused_by_default(pi, fake_pageindex, tmp_path, monkeypatch, installed):
    monkeypatch.setattr(pi.ingest, 'installed_pageindex_commit', lambda: installed)
    s = pi.common.Settings(store_dir=tmp_path / 'store')
    assert s.allow_unpinned_pageindex is False
    with pytest.raises(RuntimeError, match='--allow-unpinned-pageindex'):
        pi.ingest.ingest(_pdf(tmp_path), 'gtp', '2019', s=s)
    assert fake_pageindex.calls == []                                   # refused before any build
    assert not (tmp_path / 'store' / 'manifest.json').exists()
    assert not list((tmp_path / 'store' / 'pdfs').iterdir())


def test_flag_allows_unpinned_and_records_it(pi, fake_pageindex, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pi.ingest, 'installed_pageindex_commit', lambda: OTHER)
    rec = pi.ingest.ingest(_pdf(tmp_path), 'gtp', '2019', allow_unpinned=True,
                           s=pi.common.Settings(store_dir=tmp_path / 'store'))
    assert rec.pageindex_commit == f'unpinned: {OTHER}' and rec.index_route == 'anthropic'
    stored = json.loads((tmp_path / 'store' / 'manifest.json').read_text())
    assert stored['documents']['gtp@2019']['pageindex_commit'] == f'unpinned: {OTHER}'
    assert 'UNPINNED' in capsys.readouterr().err


def test_settings_key_allows_unpinned_and_explicit_false_overrides_it(pi, fake_pageindex, tmp_path, monkeypatch):
    monkeypatch.setattr(pi.ingest, 'installed_pageindex_commit', lambda: OTHER)
    s = pi.common.Settings(store_dir=tmp_path / 'store', allow_unpinned_pageindex=True)
    with pytest.raises(RuntimeError):
        pi.ingest.ingest(_pdf(tmp_path), 'gtp', '2019', s=s, allow_unpinned=False)
    rec = pi.ingest.ingest(_pdf(tmp_path), 'gtp', '2019', s=s)
    assert rec.pageindex_commit == f'unpinned: {OTHER}'


@pytest.mark.parametrize('argv,expected', [(['--allow-unpinned-pageindex'], True), ([], None)])
def test_cli_flag_reaches_ingest(pi, monkeypatch, capsys, argv, expected):
    seen = {}

    def fake_ingest(*args, **kw):
        seen.update(kw)
        return pi.common.DocRecord(doc_id='g@1', family='g', title='t', version='1', sha256='0' * 64,
                                   pdf_path='p', tree_path='t', page_count=1, index_mode='standard',
                                   index_model='anthropic/x', pageindex_version='0.2.10',
                                   built_at='2026-11-03T00:00:00Z')
    monkeypatch.setattr(pi.ingest, 'ingest', fake_ingest)
    assert pi.ingest.main(['x.pdf', '--family', 'g', '--version', '1', *argv]) == 0
    assert seen == {'allow_unpinned': expected}      # None -> falls back to the settings key
    assert '"doc_id": "g@1"' in capsys.readouterr().out
