#!/usr/bin/env python3
"""Render an Office document or PDF to one PNG per page/slide for visual QA.

Usage:
    python render_to_images.py <file.docx|.pptx|.xlsx|.pdf> [out_dir] [--dpi 110]
                               [--first N] [--last M]

Pipeline:
    .docx/.pptx/.xlsx  --LibreOffice (headless, throwaway profile)-->  .pdf
    .pdf               --pypdfium2 | pdftoppm | pdf2image-->           page-N.png

Prints the absolute path of every PNG written, one per line, so they can be
opened with the `read` tool. Exit code 0 on success, 1 on failure (with a hint
about which dependency is missing).
"""

from __future__ import annotations

import argparse
import glob
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OFFICE_EXTS = {".docx", ".doc", ".pptx", ".ppt", ".xlsx", ".xls", ".odt", ".odp", ".ods", ".rtf"}


def find_soffice() -> str | None:
    for name in ("soffice", "libreoffice"):
        path = shutil.which(name)
        if path:
            return path
    for candidate in (
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        "/opt/libreoffice/program/soffice",
        "/usr/lib/libreoffice/program/soffice",
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def office_to_pdf(src: Path, workdir: Path) -> Path:
    soffice = find_soffice()
    if not soffice:
        raise RuntimeError(
            "LibreOffice (soffice) not found. Install it (apt install libreoffice / brew install --cask "
            "libreoffice / winget install TheDocumentFoundation.LibreOffice), or preview with the "
            "office_render tool instead."
        )
    profile = workdir / "lo-profile"
    profile.mkdir(parents=True, exist_ok=True)
    cmd = [
        soffice,
        "--headless",
        "--norestore",
        f"-env:UserInstallation={profile.resolve().as_uri()}",
        "--convert-to",
        "pdf",
        "--outdir",
        str(workdir),
        str(src),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
    pdf = workdir / (src.stem + ".pdf")
    if not pdf.exists():
        raise RuntimeError(f"LibreOffice conversion failed:\n{proc.stdout}\n{proc.stderr}")
    return pdf


def pdf_to_pngs(pdf: Path, out_dir: Path, dpi: int, first: int, last: int | None) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in glob.glob(str(out_dir / "page-*.png")):
        os.remove(old)
    written: list[Path] = []

    try:
        import pypdfium2 as pdfium  # type: ignore

        doc = pdfium.PdfDocument(str(pdf))
        stop = min(len(doc), last) if last else len(doc)
        for i in range(first - 1, stop):
            image = doc[i].render(scale=dpi / 72).to_pil()
            target = out_dir / f"page-{i + 1}.png"
            image.save(target)
            written.append(target)
        return written
    except ImportError:
        pass

    if shutil.which("pdftoppm"):
        cmd = ["pdftoppm", "-png", "-r", str(dpi), "-f", str(first)]
        if last:
            cmd += ["-l", str(last)]
        cmd += [str(pdf), str(out_dir / "page")]
        subprocess.run(cmd, check=True, capture_output=True, timeout=240)
        # pdftoppm zero-pads page numbers; normalise to page-N.png
        for path in sorted(out_dir.glob("page-*.png")):
            num = int(path.stem.split("-")[-1])
            target = out_dir / f"page-{num}.png"
            if path != target:
                path.rename(target)
            written.append(target)
        return sorted(written, key=lambda p: int(p.stem.split("-")[-1]))

    try:
        from pdf2image import convert_from_path  # type: ignore

        images = convert_from_path(str(pdf), dpi=dpi, first_page=first, last_page=last)
        for offset, image in enumerate(images):
            target = out_dir / f"page-{first + offset}.png"
            image.save(target)
            written.append(target)
        return written
    except ImportError:
        pass

    raise RuntimeError(
        "No PDF rasterizer available. Run `pip install pypdfium2` (pure wheel, no system deps) "
        "or install poppler-utils (pdftoppm)."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input")
    parser.add_argument("out_dir", nargs="?", default=None)
    parser.add_argument("--dpi", type=int, default=110)
    parser.add_argument("--first", type=int, default=1)
    parser.add_argument("--last", type=int, default=None)
    args = parser.parse_args()

    src = Path(args.input).expanduser().resolve()
    if not src.exists():
        print(f"ERROR: {src} not found", file=sys.stderr)
        return 1
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else src.parent / f"{src.stem}_pages"

    try:
        with tempfile.TemporaryDirectory(prefix="render_") as tmp:
            workdir = Path(tmp)
            if src.suffix.lower() == ".pdf":
                pdf = src
            elif src.suffix.lower() in OFFICE_EXTS:
                pdf = office_to_pdf(src, workdir)
            else:
                print(f"ERROR: unsupported file type {src.suffix}", file=sys.stderr)
                return 1
            pages = pdf_to_pngs(pdf, out_dir, args.dpi, args.first, args.last)
    except Exception as exc:  # noqa: BLE001 - report any failure with its hint
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    for page in pages:
        print(page)
    print(f"# {len(pages)} page image(s) in {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
