"""Run after setup: python check_setup.py  -> every line should say OK."""
import os
from dotenv import load_dotenv

load_dotenv()

def check(name, fn):
    try:
        fn()
        print(f"OK    {name}")
    except Exception as e:
        print(f"FAIL  {name}: {e}")

def neo4j_check():
    from neo4j import GraphDatabase
    d = GraphDatabase.driver(os.environ["NEO4J_URI"],
                             auth=(os.environ["NEO4J_USER"], os.environ["NEO4J_PASSWORD"]))
    d.verify_connectivity()
    d.close()

def pg_check():
    import psycopg
    with psycopg.connect(os.environ["PG_DSN"]) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")

def openai_check():
    from openai import OpenAI
    v = OpenAI().embeddings.create(model="text-embedding-3-small", input="test").data[0].embedding
    assert len(v) == 1536, len(v)

def anthropic_check():
    import anthropic
    anthropic.Anthropic().messages.create(model="claude-haiku-4-5", max_tokens=5,
                                          messages=[{"role": "user", "content": "hi"}])

def legalbert_check():
    from transformers import AutoTokenizer, AutoModel
    AutoTokenizer.from_pretrained("nlpaueb/legal-bert-base-uncased")
    AutoModel.from_pretrained("nlpaueb/legal-bert-base-uncased")

def tiktoken_check():
    import tiktoken
    tiktoken.get_encoding("cl100k_base").encode("SHALL")

def libs_check():
    import fastmcp, pdfplumber, fitz, PyPDF2, litellm, ragas, langfuse  # noqa

check("Neo4j connection", neo4j_check)
check("Postgres + pgvector", pg_check)
check("OpenAI embeddings (1536-dim)", openai_check)
check("Anthropic API", anthropic_check)
check("LegalBERT download", legalbert_check)
check("tiktoken", tiktoken_check)
check("Other libraries", libs_check)
