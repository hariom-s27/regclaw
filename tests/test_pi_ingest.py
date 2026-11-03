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


def test_commit_mismatch_warns(pi, fake_pageindex, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pi.ingest, 'installed_pageindex_commit', lambda: 'unknown (not installed)')
    pdf = tmp_path / 'g.pdf'
    pdf.write_bytes(b'%PDF-1.4 other bytes')
    rec = pi.ingest.ingest(str(pdf), 'gtp', '2019', s=pi.common.Settings(store_dir=tmp_path / 'store'))
    assert rec.index_route == 'anthropic' and rec.pageindex_commit.startswith('unknown')
    assert 'pinned 50a71b6' in capsys.readouterr().err
