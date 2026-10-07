"""Download the public substitute corpus described in the local source spec."""

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "specs" / "S2_corpus_sources.md"
PDF_DIR = ROOT / "data" / "pdfs"
MANIFEST = ROOT / "data" / "corpus_manifest.json"
SLICES = Path(__file__).with_name("eu_mdr_slices.yaml")
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class Source:
    code: str
    url: str
    manual: bool = False
    optional: bool = False

    @property
    def filename(self):
        return "eu-mdr_full.pdf" if self.code == "eu-mdr" else f"{self.code}.pdf"


def read_sources(path=SPEC):
    sources = []
    codes = set()
    for row in csv.reader(Path(path).read_text(encoding="utf-8").splitlines(),
                          delimiter="|", quoting=csv.QUOTE_NONE):
        if len(row) != 6:
            continue
        cells = [cell.strip() for cell in row[1:-1]]
        code = re.fullmatch(r"`([a-z0-9]+(?:-[a-z0-9]+)*)`(?: \(optional\))?", cells[0])
        if code is None:
            continue
        url = re.search(r"https?://[^\s<>]+", cells[2])
        if url is None or code[1] in codes:
            raise ValueError(f"Missing URL or duplicate doc code: {cells[0]}")
        codes.add(code[1])
        sources.append(Source(code[1], url[0],
                              manual=cells[3].startswith("MANUAL:"),
                              optional="(optional)" in cells[0]))
    if not sources:
        raise ValueError(f"No source-table rows found in {path}")
    return sources


def is_pdf(content):
    return content.startswith(b"%PDF")


def fetch_pdf(url, session, retries=2):
    for attempt in range(retries + 1):
        try:
            with session.get(url, timeout=60, headers={"User-Agent": USER_AGENT}) as response:
                response.raise_for_status()
                content = response.content
            if not is_pdf(content):
                raise ValueError("Response does not start with %PDF")
            return content
        except (OSError, ValueError):
            if attempt == retries:
                raise
            time.sleep(attempt + 1)


def download_source(source, pdf_dir, session, previous=None):
    path = Path(pdf_dir) / source.filename
    entry = {"url": source.url, "bytes": 0, "sha256": None,
             "downloaded_at": None, "status": "manual"}
    try:
        if path.exists():
            content = path.read_bytes()
            if not is_pdf(content):
                raise ValueError(f"Existing file does not start with %PDF: {path}")
            digest = hashlib.sha256(content).hexdigest()
            downloaded_at = (previous or {}).get("downloaded_at")
            if not downloaded_at or (previous or {}).get("sha256") != digest:
                downloaded_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
            print(f"SKIP: {source.code} already exists", flush=True)
        elif source.manual:
            content = None
        else:
            print(f"Downloading {source.code}...", flush=True)
            content = fetch_pdf(source.url, session)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            downloaded_at = datetime.now(timezone.utc).isoformat()
        if content is not None:
            entry.update(bytes=len(content), sha256=digest,
                         downloaded_at=downloaded_at, status="ok")
            return entry
    except (OSError, ValueError) as error:
        print(f"{source.code}: {error}", file=sys.stderr, flush=True)
    print(f"MANUAL: download {source.code} from {source.url} "
          f"and save as data/pdfs/{source.filename}", flush=True)
    return entry


def slice_eu_mdr(pdf_dir=PDF_DIR, config_path=SLICES):
    import yaml

    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    parts = config["parts"]
    if not parts:
        raise ValueError("EU MDR slicing needs at least one part")
    if any(part["pages"] == "TODO" for part in parts):
        print("REMINDER: fill 1-based inclusive ranges in corpus/eu_mdr_slices.yaml; "
              "eu-mdr_full.pdf remains unsliced.", flush=True)
        return False
    ranges = [part["pages"] for part in parts]
    for pages in ranges:
        if (not isinstance(pages, list) or len(pages) != 2
                or any(type(page) is not int for page in pages)
                or not 1 <= pages[0] <= pages[1]):
            raise ValueError("Each EU MDR range must be [first, last], 1-based inclusive")

    import pymupdf

    pdf_dir = Path(pdf_dir)
    destination = pdf_dir / "eu-mdr.pdf"
    if destination.exists():
        print("SKIP: eu-mdr.pdf already exists", flush=True)
        return True
    if not (pdf_dir / "eu-mdr_full.pdf").exists():
        print("REMINDER: download eu-mdr_full.pdf before slicing EU MDR.", flush=True)
        return False
    with pymupdf.open(pdf_dir / "eu-mdr_full.pdf") as full, pymupdf.open() as sliced:
        if any(last > len(full) for first, last in ranges):
            raise ValueError("EU MDR range exceeds the physical PDF page count")
        for first, last in ranges:
            sliced.insert_pdf(full, from_page=first - 1, to_page=last - 1)
        sliced.save(destination, garbage=4, deflate=True)
    print(f"Sliced eu-mdr.pdf: {sum(last - first + 1 for first, last in ranges)} pages", flush=True)
    return True


def main():
    # requests is needed for downloading, not for the offline PDF-check tests.
    import requests

    sources = read_sources()
    previous = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {}
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    manifest = {}
    with requests.Session() as session:
        for source in sources:
            manifest[source.code] = download_source(source, PDF_DIR, session, previous.get(source.code))
            MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    slice_eu_mdr()
    ok = sum(entry["status"] == "ok" for entry in manifest.values())
    print(f"Manifest: {len(manifest)} entries, {ok} ok, {len(manifest) - ok} manual", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
