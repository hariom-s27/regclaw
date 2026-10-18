"""Explicit full-text setup. Retrieval itself never creates indexes."""
import argparse
import os

FULLTEXT_INDEX = 'clause_text_ft'


def setup_fulltext_index(driver, database='neo4j'):
    driver.execute_query(
        f'CREATE FULLTEXT INDEX {FULLTEXT_INDEX} IF NOT EXISTS '
        'FOR (c:Clause) ON EACH [c.title, c.text]', database_=database)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='neo4j')
    args = parser.parse_args(argv)
    if os.getenv('REGCLAW_STUB') == '1':
        raise RuntimeError('retrieval-setup requires an explicit live environment')
    from dotenv import load_dotenv
    from neo4j import GraphDatabase
    load_dotenv()
    with GraphDatabase.driver(os.environ['NEO4J_URI'],
            auth=(os.environ['NEO4J_USER'], os.environ['NEO4J_PASSWORD'])) as driver:
        setup_fulltext_index(driver, args.database)
    return 0
