# Engineering journal

This journal records the rebuild chronology. Implementation links and regression
tests confirm the corresponding behavior; historical run counts are stated as
recorded facts rather than reconstructed from current artifacts.

## 2026-10-08: acquisition artifacts

Commit `efad881` records EU slices, acquisition metadata refresh, HTML-print
provenance, and a scanned-document check. The corpus step was closed and pushed
on 2026-10-15. Slicing is implemented in
[corpus/download.py](../corpus/download.py), with ranges in
[corpus/eu_mdr_slices.yaml](../corpus/eu_mdr_slices.yaml).

## 2026-10-15: initial parser checkpoint

The initial batch produced 966 clauses and revealed 82 unstable `~N` ids,
encoding damage, body coverage gaps, and wrapped-title problems.
The checkpoint was followed by targeted parser repairs rather than tuning a
clause count. Regression evidence is in [tests/test_ingest.py](../tests/test_ingest.py).

## 2026-10-15: stable ids, encoding, and OJ body text

The parser version 2.1 run produced 1,009 clauses, zero `~N` renames, and zero
encoding defects. Thus the recorded collision count fell from 82 to zero.
Commit `871bb10` contains the fixes. The earlier encoding defect total is not
reasserted; the confirmed post-fix count is zero.
Repeating-header handling had removed genuine EU OJ body lines; the repaired
coverage accounting no longer hides unowned body text.
Confirmation: `test_real_body_lines_survive_header_filter`,
`test_real_encoding_and_superscript_exponents`, `test_real_wrapped_titles`, and
`test_coverage_does_not_exclude_unowned_body` in
[tests/test_ingest.py](../tests/test_ingest.py).

## 2026-10-15: EPA Appendix and forms

The parser version 2.2 run produced 1,133 clauses. EPA Appendix A contributed
69 clauses; the India fixes handled 43 forms and stripped 65 amendment notes.
Commit `b00238f` records that round, including linked footnotes.
Confirmation: `test_epa_appendix_real_heading_depth_tables_and_detached_labels`
and `test_india_annexure_forms_and_editorial_notes_everywhere` in
[tests/test_ingest.py](../tests/test_ingest.py).

## 2026-10-15: merged definitions and the Preface correction

The follow-up identified nine merged EU Article definitions and five nesting
errors. The version 2.3 run fixed the EU boundaries and split EPA named tables,
producing 1,143 clauses before the parser was frozen (commit `96108f9`).
The version 2.2 Preface rule was too broad. It was reverted for substantive
regulatory front matter; boilerplate prefaces are still skipped while the
Indian essential-principles regulatory preface is retained.
Confirmation: `test_eu_real_definition_wrap_updates_previous`,
`test_eu_real_article_2_roman_v_and_short_points`,
`test_boilerplate_preface_is_logged_as_skipped_section`, and
`test_india_ep_regulatory_preface_is_retained` in
[tests/test_ingest.py](../tests/test_ingest.py).

## 2026-10-15: Docker and WSL setup

WSL 3.0.3 with Ubuntu and Docker Desktop 4.95.0, using Docker 29.8.2, were installed.
The shell initially retained an old PATH, so Docker was not found until PATH was
reloaded. The engine remained on starting while RAM use was 96% on a 16 GB laptop;
closing applications allowed it to start. A WSL memory cap of 4 GB was optional.
The Neo4j and pgvector images were pulled and containers started.
Current image pins and ports are in [docker-compose.yml](../docker-compose.yml).
This is the installation chronology, not a claim about current service health.

## 2026-10-15: first live graph load

The first clauses-only load created 1,159 nodes and 1,163 relationships, matching
the plan. The subsequent reload created zero nodes and zero relationships.
The original check command did not itself measure reload counters, so its
idempotency row remained WARN even though a separate reload had succeeded.
That distinction prompted the measured-check fix below.
The loader and check definitions are in [graph/build_graph.py](../graph/build_graph.py)
and [graph/checks.py](../graph/checks.py).

## 2026-10-16: NumPy markers and measured checks

The local Python 3.11 environment could not install the unconditional NumPy 2.5.3
pin. The fix selects 2.5.3 for Python at least 3.12 and 2.4.6 below it
([requirements.txt](../requirements.txt)); CI also selects Python 3.11.
`--load --checks` now performs a second load and evaluates returned counters.
The installed notification filter suppresses unrecognized-schema notices.
Confirmation: [tests/test_graph.py](../tests/test_graph.py) and
[tests/test_env_template.py](../tests/test_env_template.py).

## 2026-10-16: scorer policy and manual tracing

The scorer now publishes raw and policy views together; retained official
heading parents no longer count as policy errors. The parser is unchanged.
Manual retriever, model, and PageIndex spans preserve offline outputs and avoid
SDK import when disabled. The recorded S14 test total was 285.
Confirmation: [tests/test_structure_gold.py](../tests/test_structure_gold.py),
[tests/test_tracing.py](../tests/test_tracing.py), and [results](results.md).

## 2026-10-16: managed-transaction driver fix

The earlier eager Cypher smoke returned six rows, but the legacy helper passed
a Query object to a managed transaction and raised TypeError.
The fix keeps `execute_read`, applies `unit_of_work(timeout=...)` to the callback,
and passes a plain string to `tx.run`. The regression fake rejects Query objects
and checks timeout and in-transaction consumption.
Confirmation: [tests/test_retrieval_driver.py](../tests/test_retrieval_driver.py).
The fresh localhost smoke and graph reload both failed during the Bolt handshake;
current live verification remains pending in [results](results.md).

## 2026-10-16: public documentation cleanup

Scope and licence text now follow the agreed templates. Citations point to tracked
implementation, tests, and reproducing commands. The journal includes the confirmed
setup chronology and Preface correction. No private notes or original-system
measurements are published. Synthetic fixture timings stay out of reported results.
