---
status: proposed
date: 2026-10-15
conflict-id: C-7
evidence: reasoned
---
# 0007: pdfplumber scanned check and PyMuPDF page fetch

## Context and Problem Statement

Tree ingestion must detect unusable text layers, while the answer agent needs consistent physical-page text access.

## Decision Drivers

- Preserve implemented data contracts and document provenance.
- Make the choice reviewable with local code, tests, or recorded output.
- Keep unrun quality and performance claims explicitly pending.

## Considered Options

- Separate inspection and page-fetch backends.
- Use a single backend for both.
- Assume every PDF has a usable text layer.

## Decision Outcome

Propose pdfplumber for the scanned-document inspection and PyMuPDF for fetching pages. Keep the same physical-page convention across agent and evaluation.

## Consequences

The roles are explicit in existing code. OCR quality and end-to-end Part II behavior still require a real corpus run.

## Confirmation

none yet for the complete Part II backend workflow.

## Evidence and Status

Sources: pageindex/inspect_pdf.py; pageindex/pi_common.py::fetch_pages.
A test cited above confirms only the behavior it exercises. Pending live work
is not replaced by synthetic fixtures.
