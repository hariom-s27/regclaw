"""In-memory tests: Client(mcp) talks to the server object directly, no subprocess, no stdio.

Run: REGCLAW_STUB=1 pytest -q test_server.py
"""
import json
import os

os.environ.setdefault("REGCLAW_STUB", "1")

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from server import mcp

# async tests run via asyncio_mode = auto (pytest.ini); no module-wide mark needed.


@pytest.fixture
async def client():
    async with Client(mcp) as c:  # runs the lifespan, like a real session
        yield c


async def test_lists_tools_and_resources(client):
    tools = {t.name for t in await client.list_tools()}
    assert tools == {"query_regulatory_knowledge", "get_applicable_standards"}

    uris = {str(r.uri) for r in await client.list_resources()}
    assert uris == {"regclaw://modules", "regclaw://actors", "regclaw://lifecycle-phases"}

    templates = {t.uri_template for t in await client.list_resource_templates()}
    assert "regclaw://modules/{module_id}" in templates


async def test_tool_marked_read_only(client):
    tool = next(t for t in await client.list_tools() if t.name == "query_regulatory_knowledge")
    assert tool.annotations.read_only_hint is True
    assert tool.output_schema is not None  # Pydantic return type -> structured output schema


async def test_query_regulatory_knowledge(client):
    res = await client.call_tool("query_regulatory_knowledge",
                                 {"query": "ethylene oxide sterilization residuals", "top_k": 3})
    data = res.structured_content
    assert data["candidate_count"] == 4
    ranked = [c for c in data["clauses"] if c["source"] == "ranked"]
    assert len(ranked) == 3
    assert data["max_score"] == max(c["score"] for c in ranked)
    assert any(c["source"] == "neighbour" for c in data["clauses"])


async def test_input_validation_rejects_bad_args(client):
    with pytest.raises(ToolError):
        await client.call_tool("query_regulatory_knowledge", {"query": "x"})  # min_length=3
    with pytest.raises(ToolError):
        await client.call_tool("query_regulatory_knowledge", {"query": "valid query", "top_k": 500})


async def test_get_applicable_standards(client):
    profile = {"device_name": "Knee X", "components": [{"name": "implant", "contact_nature": "implant",
               "contact_site": "tissue_bone", "exposure": {"pattern": "single", "hours_per_use": 8760},
               "materials": [{"name": "Ti", "material_class": "metal_alloy"}], "sterilization": "ethylene_oxide"}]}
    res = await client.call_tool("get_applicable_standards", {"device_profile": profile})
    by_std = {d["part"]: d["label"] for d in res.structured_content["standards"]}
    assert by_std["ISO 10993-1"] == "mandatory"
    assert by_std["ISO 10993-7"] == "mandatory"
    assert by_std["ISO 10993-6"] == "mandatory"


async def test_standards_v1_schema_and_description(client):
    tool = next(t for t in await client.list_tools() if t.name == 'get_applicable_standards')
    assert 'mandatory = must be addressed in the biological evaluation, not necessarily tested' in tool.description
    # FastMCP resolves local schema references into the wire input schema.
    profile = tool.input_schema['properties']['device_profile']
    assert 'components' in profile['required']
    assert 'contact_nature' in profile['properties']['components']['items']['properties']
    assert profile['additionalProperties'] is False


async def test_standards_rejects_legacy_profile(client):
    with pytest.raises(ToolError):
        await client.call_tool('get_applicable_standards', {'device_profile': {
            'device_name': 'legacy', 'device_type': 'implant', 'body_contact': 'implant',
            'contact_tissue': 'bone', 'contact_duration': 'long_term'}})


async def test_resources(client):
    contents = await client.read_resource("regclaw://modules")
    assert json.loads(contents[0].text)[0]["clauses"] == 1149

    contents = await client.read_resource("regclaw://modules/biocompatibility")
    assert json.loads(contents[0].text)["module_id"] == "biocompatibility"

    for uri in ("regclaw://actors", "regclaw://lifecycle-phases"):
        assert json.loads((await client.read_resource(uri))[0].text)


async def test_unknown_module_errors(client):
    with pytest.raises(Exception):
        await client.read_resource("regclaw://modules/nope")


def test_real_mode_maps_hybrid_retriever_output():
    """Backend.retrieve() must map HybridRetriever (R4) Hits/RunLog onto the MCP schema,
    including neighbours and the NONE obligation. Fake retriever: no DB needed."""
    from types import SimpleNamespace as NS
    from server import Backend

    hits = [NS(clause_id="fda-iso10993-1:III.A", score=0.6465, via="ranked", number="III.A",
               title="Risk assessment", standard="fda-iso10993-1", obligation="SHOULD", text="..."),
            NS(clause_id="fda-iso10993-1:III", score=0.58, via="neighbour:parent:fda-iso10993-1:III.A",
               number="III", title="Risk management", standard="fda-iso10993-1", obligation="NONE", text="..."),
            NS(clause_id="eu-mdr:10.4.1", score=0.5, via="ranked", number="10.4.1", title="Substances",
               standard="eu-mdr", obligation="", text="...")]
    run = NS(candidates=72, max_score=0.6465, filter_path="text2cypher", warnings=[])
    b = Backend(retriever=NS(retrieve=lambda q, top_k: (hits, run)))

    res = b.retrieve("mandatory requirements for biological evaluation", 10)
    assert res.candidate_count == 72 and res.max_score == 0.6465
    assert [c.source for c in res.clauses] == ["ranked", "neighbour", "ranked"]
    assert res.clauses[1].obligation == "NONE"
    assert res.clauses[2].obligation is None  # empty string from Neo4j -> None


def test_real_mode_empty_result_raises_tool_error():
    from types import SimpleNamespace as NS
    from server import Backend
    run = NS(candidates=0, max_score=None, filter_path="all_clauses", warnings=[])
    b = Backend(retriever=NS(retrieve=lambda q, top_k: ([], run)))
    with pytest.raises(ToolError):
        b.retrieve("anything", 5)


async def test_stdio_subprocess_end_to_end():
    """Launch server.py as a real stdio subprocess (what Claude Desktop does).
    Fails if anything pollutes stdout, because the JSON-RPC stream breaks."""
    from pathlib import Path
    from fastmcp.client.transports import StdioTransport
    import sys
    here = Path(__file__).resolve().parent
    transport = StdioTransport(command=sys.executable, args=[str(here / "server.py")],
                               env={"REGCLAW_STUB": "1"}, cwd=str(here))
    async with Client(transport) as c:
        res = await c.call_tool("query_regulatory_knowledge", {"query": "knee implant biocompatibility tests"})
        assert res.structured_content["clauses"]
