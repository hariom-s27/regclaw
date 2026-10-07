"""Rasterise the FDA ISO 10993-1 guidance to an image-only OCR fixture."""

from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]


def make_scanned_test(source, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open(source) as original, pymupdf.open() as scanned:
        for page in original:
            pixmap = page.get_pixmap(dpi=150, colorspace=pymupdf.csRGB, alpha=False)
            image_page = scanned.new_page(width=page.rect.width, height=page.rect.height)
            image_page.insert_image(image_page.rect, pixmap=pixmap)
        scanned.save(destination, garbage=4, deflate=True)
        expected_pages = len(original)
    with pymupdf.open(destination) as scanned:
        assert len(scanned) == expected_pages, "Rasterisation changed the page count"
        assert all(page.get_text() == "" for page in scanned), "Scanned PDF has a text layer"
    return expected_pages


def main():
    source = ROOT / "data" / "pdfs" / "fda-iso10993-1.pdf"
    destination = ROOT / "data" / "pdfs" / "scanned_test.pdf"
    if not source.exists():
        print("MANUAL: fda-iso10993-1.pdf is required before creating scanned_test.pdf")
        return 1
    pages = make_scanned_test(source, destination)
    print(f"scanned_test.pdf: {pages} pages, 0 text-layer pages (150 dpi)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
