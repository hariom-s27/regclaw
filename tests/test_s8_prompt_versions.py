"""B3 prompt versions: v1.0 frozen default, v1.1 = v1.0 + rule 8 + few-shot 6. Fakes only."""
import hashlib
import json
from pathlib import Path
import re

import pytest
import yaml

from llm.azure_openai import AzureOpenAIAdapter
from llm.cypher import request
from retrieval.cypher_cache import JsonlCache, generation_key
from retrieval.hybrid_retriever import (
    DEFAULT_PROMPT_VERSION, FEW_SHOTS, FEW_SHOT_6_V1_1, OBLIGATION_RULE_V1_1, PROMPT_VERSIONS,
    RunLog, SCHEMA_FALLBACK, SYSTEM_PROMPT, SYSTEM_PROMPT_V1_1, has_obligation_filter,
    prompt_hashes, render_fewshots, render_prompt, validate_cypher,
)
from test_s8_cypher import offline, read_rows, stage1  # noqa: F401  (fixture re-export)
from test_tracing import fake, retriever  # noqa: F401  (fixture re-export)

ROOT = Path(__file__).resolve().parents[1]

# B3 live sanity queries (non-eval). S1 is the B2 smoke query.
B2_SMOKE_QUERY = 'cytotoxicity test requirements for wound dressings'
SANITY_QUERIES = {
    'S1': B2_SMOKE_QUERY,
    'S2': 'mandatory pyrogenicity testing for infusion sets',
    'S3': "what must an investigator's brochure contain",
}

# Captured from the pre-B3 code (HEAD d6fdf76) before any edit: the v1.0 key must stay byte-identical.
PRE_B3_V1_KEY = {
    "attempt_no": 2, "effort": "low",
    "feedback_sha": "0fbc285dad2b5e6afbbace2df225059f714128ad00b8a26bf23fc161933bc727",
    "fewshot_sha": "79cdc038a4315c95bab09de041a07591015c069bf5d0ae522e4bce2d963b20a2", "gen_index": 3,
    "prompt_template_sha": "137df5ec3edd36652a2647db7f17da7aa6f816b087bac08389402ff1c9e4a9f1",
    "provider": "azure_openai", "qid": "SYN-KEY", "query_text": "synthetic key fixture",
    "query_text_sha": "10a8f721826c2718225cee096221d7d12a7f5ff4643153325ce0bc4e24887e8e",
    "rendered_prompt_sha": "a66d44ddca7799d6762fd7b27296dabbf01979d1e59c633dcc70a5320c8bc00f",
    "requested_model": "gpt-5-mini", "role": "s8_cypher",
    "schema_sha": "c3c8247a22f42433004b90e6236f99ccf3c9a929b609e3659c82740e8b7bf9e4", "variant": "b"}
PRE_B3_V1_RENDERED_SHA = 'a66d44ddca7799d6762fd7b27296dabbf01979d1e59c633dcc70a5320c8bc00f'


def fixture_key(prompt_version=None):
    extra = {} if prompt_version is None else {'prompt_version': prompt_version}
    return generation_key('synthetic key fixture', qid='SYN-KEY', variant='b', gen_index=3,
                          hashes=prompt_hashes(SCHEMA_FALLBACK, prompt_version or '1.0'),
                          provider='azure_openai', requested_model='gpt-5-mini', effort='low',
                          attempt_no=2, feedback='fb', prev='pv', **extra)


def leak_violations(texts):
    """Indices of `texts` that overlap (substring either way, case-insensitive) any frozen query/rephrase.
    Returns indices only so frozen texts are never printed."""
    raw = (ROOT / 'retrieval_eval/queries.yaml').read_bytes()
    assert hashlib.sha256(raw.replace(b'\r\n', b'\n')).hexdigest() == (
        '83d7813873d6d9e47a732db8778f68c18cfb4654834acfa16ca6847457affc7c')
    rows = yaml.safe_load(raw)['queries']
    assert len(rows) == 16
    frozen = [row[k].casefold() for row in rows for k in ('query', 'rephrase_b')]
    assert len(frozen) == 32
    return [i for i, t in enumerate(texts)
            if any(t.casefold() in f or f in t.casefold() for f in frozen)]


# ---------- default = 1.0, unchanged ----------
def test_default_is_v1_and_matches_freeze_and_pre_b3_render():
    assert DEFAULT_PROMPT_VERSION == '1.0'
    assert PROMPT_VERSIONS['1.0'][0] is SYSTEM_PROMPT and PROMPT_VERSIONS['1.0'][1] is FEW_SHOTS
    frozen = json.loads((ROOT / 'retrieval_eval/s8_prompt_freeze.json').read_text(encoding='utf-8'))
    hashes = prompt_hashes(SCHEMA_FALLBACK)
    assert hashes == prompt_hashes(SCHEMA_FALLBACK, '1.0')
    assert hashes['prompt_template_sha'] == frozen['template_sha']
    assert hashes['fewshot_sha'] == frozen['fewshot_sha']
    assert hashes['rendered_prompt_sha'] == PRE_B3_V1_RENDERED_SHA
    assert render_prompt(SCHEMA_FALLBACK) == render_prompt(SCHEMA_FALLBACK, '1.0')
    assert len(FEW_SHOTS) == 5 and RunLog('q').prompt_version == '1.0'


# ---------- v1.1 = v1.0 + exactly two additions ----------
def test_v1_1_is_v1_plus_rule_8_and_few_shot_6_only():
    template, shots = PROMPT_VERSIONS['1.1']
    assert template is SYSTEM_PROMPT_V1_1 and shots[:5] == FEW_SHOTS and shots[5] is FEW_SHOT_6_V1_1
    assert len(shots) == 6
    assert OBLIGATION_RULE_V1_1 == (
        "8. Add an obligation filter only when the question explicitly restricts the strength of the provision: "
        "'mandatory', 'binding' or 'legally required' → SHALL; 'recommended' or 'non-binding' → SHOULD; "
        "'optional' → MAY. Question words such as 'must', 'should' or 'need to', and nouns such as "
        "'requirements', 'tests', 'rules' or 'guidance', do not by themselves imply a filter.\n")
    anchor = '7. If the question names no entity or obligation, fall back to the full-text index on clause text.\n'
    assert SYSTEM_PROMPT_V1_1 == SYSTEM_PROMPT.replace(anchor, anchor + OBLIGATION_RULE_V1_1)
    assert SYSTEM_PROMPT_V1_1.index(OBLIGATION_RULE_V1_1) < SYSTEM_PROMPT_V1_1.index('EXAMPLES')
    shot6 = (f"Q: {FEW_SHOT_6_V1_1['q']}\nCYPHER: {FEW_SHOT_6_V1_1['cypher']}\n"
             f"PARAMS: {json.dumps(FEW_SHOT_6_V1_1['params'], ensure_ascii=False, sort_keys=True)}\n")
    assert render_fewshots('1.1') == render_fewshots('1.0') + '\n' + shot6
    v10, v11 = render_prompt(SCHEMA_FALLBACK, '1.0'), render_prompt(SCHEMA_FALLBACK, '1.1')
    assert v11 == v10.replace(anchor, anchor + OBLIGATION_RULE_V1_1) + '\n' + shot6
    h10, h11 = prompt_hashes(SCHEMA_FALLBACK, '1.0'), prompt_hashes(SCHEMA_FALLBACK, '1.1')
    assert h10['schema_sha'] == h11['schema_sha']
    assert all(h10[k] != h11[k] for k in ('prompt_template_sha', 'fewshot_sha', 'rendered_prompt_sha'))


def test_few_shot_6_has_no_obligation_filter_and_uses_schema_only():
    shot = FEW_SHOT_6_V1_1
    assert shot['q'] == 'irritation testing requirements for surgical gloves'
    assert not has_obligation_filter(shot['cypher'])
    assert all(set(p) == {'name', 'value'} for p in shot['params'])
    assert set(re.findall(r'\$(\w+)', shot['cypher'])) == {p['name'] for p in shot['params']}
    labels = set(re.findall(r'^\s{2}(\w+) \{', SCHEMA_FALLBACK, re.M))
    validated, warnings = validate_cypher(shot['cypher'], labels, {'BELONGS_TO', 'MENTIONS', 'REFERENCES'})
    assert validated.endswith('RETURN DISTINCT c.id AS clause_id') and not warnings
    # Same structure as v1 few-shot 3 (DeviceType OR BiologicalEffect MENTIONS) plus text/title contains.
    assert 'DeviceType' in shot['cypher'] and 'BiologicalEffect' in shot['cypher']
    assert 'toLower(c.text) CONTAINS' in shot['cypher'] and 'toLower(c.title) CONTAINS' in shot['cypher']


def test_unknown_version_raises(monkeypatch, tmp_path):
    for fn in (render_fewshots, lambda v: render_prompt(SCHEMA_FALLBACK, v),
               lambda v: prompt_hashes(SCHEMA_FALLBACK, v)):
        with pytest.raises(ValueError, match='prompt_version'):
            fn('2.0')
    r = retriever(monkeypatch, tmp_path)
    with pytest.raises(ValueError, match='prompt_version'):
        r.retrieve('synthetic question', prompt_version='1')
    assert not r.snapshots  # failed before any stage ran


# ---------- cache key ----------
def test_v1_0_key_is_byte_identical_to_pre_b3():
    assert fixture_key() == PRE_B3_V1_KEY
    assert fixture_key('1.0') == PRE_B3_V1_KEY
    assert json.dumps(fixture_key('1.0'), sort_keys=True) == json.dumps(PRE_B3_V1_KEY, sort_keys=True)


def test_v1_1_key_differs_and_carries_version():
    k11 = fixture_key('1.1')
    assert k11 != PRE_B3_V1_KEY and k11['prompt_version'] == '1.1'
    assert {k: v for k, v in k11.items() if k not in
            {'prompt_version', 'prompt_template_sha', 'fewshot_sha', 'rendered_prompt_sha'}} == {
        k: v for k, v in PRE_B3_V1_KEY.items() if k not in
            {'prompt_template_sha', 'fewshot_sha', 'rendered_prompt_sha'}}


def test_no_cross_version_replay(tmp_path):
    cache = JsonlCache(tmp_path / 'cypher_cache.jsonl')
    cache.append(dict(key=fixture_key('1.0'), reusable=True))
    assert cache.find(fixture_key('1.0')) and cache.find(fixture_key('1.1')) is None
    other = JsonlCache(tmp_path / 'other.jsonl')
    other.append(dict(key=fixture_key('1.1'), reusable=True))
    assert other.find(fixture_key('1.1')) and other.find(fixture_key('1.0')) is None


def test_stage1_versions_miss_each_other_and_rows_record_version(tmp_path):
    r, calls = stage1(tmp_path, stops=['end_turn'] * 4)
    def run(version):
        log = RunLog('synthetic question', qid='SYN1', prompt_version=version)
        r.stage1_filter(log.query, log, None)
        return log
    run('1.0'); run('1.1')
    assert len(calls) == 2  # v1.1 did not replay the v1.0 row
    assert calls[0]['system'] == render_prompt(SCHEMA_FALLBACK, '1.0')
    assert calls[1]['system'] == render_prompt(SCHEMA_FALLBACK, '1.1')
    assert all('prompt_version' not in c for c in calls)  # trace-only, never sent
    run('1.0'); run('1.1')
    assert len(calls) == 2  # each version replays its own row
    rows = read_rows(r)
    assert [row['prompt_version'] for row in rows] == ['1.0', '1.1']
    assert 'prompt_version' not in rows[0]['key'] and rows[1]['key']['prompt_version'] == '1.1'


# ---------- RunLog + tracing ----------
@pytest.mark.parametrize('version', ['1.0', '1.1', None])
def test_version_in_runlog_cache_row_and_span(monkeypatch, fake, tmp_path, version):
    r = retriever(monkeypatch, tmp_path)
    kwargs = {} if version is None else {'prompt_version': version}
    _, run = r.retrieve('synthetic question', **kwargs)
    expected = version or '1.0'
    assert run.prompt_version == expected and run.to_dict()['prompt_version'] == expected
    rows = [json.loads(line) for line in r.cypher_cache.path.read_text().splitlines()]
    assert rows and all(row['prompt_version'] == expected for row in rows)
    spans = [s for s in fake.spans if s['name'] == 'cypher.generate']
    assert len(spans) == 1 and spans[0]['metadata']['prompt_version'] == expected
    assert {s['name'] for s in fake.spans} == {'retriever', 'neo4j.cypher', 'cypher.generate', 'embed.query',
                                                'pgvector.rank', 'neighbour.expand', 'text.fetch'}


def test_azure_strips_version_and_tags_span(monkeypatch, fake):
    from types import SimpleNamespace as NS
    calls = []
    def create(**kw):
        calls.append(kw)
        return NS(model='synthetic-azure-version', usage=None, choices=[NS(
            finish_reason='stop', message=NS(content=json.dumps(dict(cypher='MATCH (c:Clause) RETURN c.id AS clause_id',
                                                                       params=[])), refusal=None))])
    monkeypatch.setenv('AZURE_OPENAI_ENDPOINT', 'https://offline.invalid')
    client = AzureOpenAIAdapter('s8_cypher', sdk_client=NS(chat=NS(completions=NS(create=create))))
    req = {**request('synthetic system', [dict(role='user', content='synthetic question')]), 'prompt_version': '1.1'}
    client.generate(req)
    assert 'prompt_version' not in calls[0] and req['prompt_version'] == '1.1'
    span = next(s for s in fake.spans if s['name'] == 'cypher.generate')
    assert span['metadata']['prompt_version'] == '1.1'


# ---------- leak ----------
def test_leak_v1_1_few_shots_and_b3_sanity_queries():
    few_shot_questions = [e['q'] for e in PROMPT_VERSIONS['1.1'][1]]
    assert len(few_shot_questions) == 6
    leaked = leak_violations(few_shot_questions + list(SANITY_QUERIES.values()))
    assert not leaked, f'Frozen query/rephrase overlap at checked-text indices {leaked}'
    rendered = (render_prompt(SCHEMA_FALLBACK, '1.1')).casefold()
    assert not leak_violations([rendered])
    assert B2_SMOKE_QUERY.casefold() not in {q.casefold() for q in few_shot_questions}
    assert not any(q.casefold() in {f.casefold() for f in few_shot_questions} for q in SANITY_QUERIES.values())
