"""Dependency-free minimal PDF writer for synthetic demo documents.

Why not reportlab: the demo should run from a plain checkout with the app's own
requirements. Text is Latin-only (the forms are English), Helvetica WinAnsi, one
content stream per page, real xref table so pdfplumber/pypdfium2 extract the text
layer like they would from a born-digital form.
"""
from __future__ import annotations

from pathlib import Path


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(path: Path, pages: list[list[str]], *, font_size: int = 11, leading: int = 15,
              margin: int = 56, page_w: int = 595, page_h: int = 842) -> Path:
    """pages: list of pages; each page is a list of text lines (Latin-1 safe)."""
    objs: list[bytes] = []

    def add(obj: str | bytes) -> int:
        objs.append(obj.encode("latin-1") if isinstance(obj, str) else obj)
        return len(objs)               # 1-based object id

    font_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    page_ids: list[int] = []
    pages_id_placeholder = None
    content_ids = []
    for lines in pages:
        y = page_h - margin
        parts = [f"BT /F1 {font_size} Tf {leading} TL {margin} {y} Td"]
        for ln in lines:
            parts.append(f"({_esc(ln)}) Tj T*")
        parts.append("ET")
        stream = "\n".join(parts).encode("latin-1", "replace")
        cid = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        content_ids.append(cid)
    pages_id = len(objs) + len(pages) + 1       # reserve: page objs then Pages
    for cid in content_ids:
        page_ids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {page_w} {page_h}] "
                            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {cid} 0 R >>"))
    kids = " ".join(f"{p} 0 R" for p in page_ids)
    real_pages_id = add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>")
    assert real_pages_id == pages_id, (real_pages_id, pages_id)
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs)+1}\n".encode() + b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs)+1} /Root {catalog_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(out))
    return path
