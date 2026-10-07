# regclaw-rebuild

An independent rebuild of regulatory retrieval systems using public documents: an ISO 10993 knowledge graph in Neo4j, pgvector hybrid retrieval, and a FastMCP stdio server (Part I); PageIndex retrieval and Ragas evaluation (Part II); and Langfuse tracing and run summaries.

Independent rebuild, on public substitute documents, of systems I worked on during my internship.

## Architecture

TODO

## Quickstart

Use Python 3.11 and install `mcp/requirements_mcp.txt` and `pytest` in a virtual environment. Implementation files are in the root-level `mcp/`, `retrieval/`, `pageindex/`, `eval/`, `observability/`, and `obligation/` folders. Run `tasks.py` from the repo root; tests run from their own folders.

```powershell
python tasks.py up
python tasks.py check
python tasks.py test
```
