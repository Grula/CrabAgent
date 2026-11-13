# Copyright (c) Anthropic, PBC.
# Licensed under the Apache License, Version 2.0 (https://www.apache.org/licenses/LICENSE-2.0).
# Adapted from Anthropic claude-code skills/pdf/scripts/convert_pdf_to_images.py.
# Modified for CrabAgent: pypdfium2 / pdftoppm fallbacks, --dpi and --max-dim options,
# prints absolute paths for the read tool.
"""Render each PDF page to page_N.png (for visual review or form-field placement).

Usage: python convert_pdf_to_images.py <input.pdf> <output_dir> [--dpi 150] [--max-dim 1600]

Rasterizer order: pypdfium2 (pip install pypdfium2) -> pdf2image -> pdftoppm (poppler).
Images larger than --max-dim on either side are scaled down; the printed size is the
image size to use for image-coordinate bounding boxes.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile


def render_pages(pdf_path, dpi):
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(pdf_path)
        return [page.render(scale=dpi / 72).to_pil() for page in doc]
    except ImportError:
        pass
    try:
        from pdf2image import convert_from_path

        return convert_from_path(pdf_path, dpi=dpi)
    except ImportError:
        pass
    if shutil.which("pdftoppm"):
        from PIL import Image

        tmp = tempfile.mkdtemp(prefix="pdfpages_")
        subprocess.run(["pdftoppm", "-png", "-r", str(dpi), pdf_path, os.path.join(tmp, "p")],
                       check=True, capture_output=True)
        files = sorted(os.listdir(tmp), key=lambda n: int(n.rsplit("-", 1)[1].split(".")[0]))
        images = [Image.open(os.path.join(tmp, n)).copy() for n in files]
        shutil.rmtree(tmp, ignore_errors=True)
        return images
    raise RuntimeError("No rasterizer found: pip install pypdfium2 (or install poppler-utils)")


def convert(pdf_path, output_dir, dpi=150, max_dim=1600):
    os.makedirs(output_dir, exist_ok=True)
    images = render_pages(pdf_path, dpi)
    for i, image in enumerate(images):
        width, height = image.size
        if width > max_dim or height > max_dim:
            scale_factor = min(max_dim / width, max_dim / height)
            image = image.resize((int(width * scale_factor), int(height * scale_factor)))
        image_path = os.path.abspath(os.path.join(output_dir, f"page_{i + 1}.png"))
        image.save(image_path)
        print(f"{image_path} (size: {image.size[0]}x{image.size[1]})")
    print(f"Converted {len(images)} pages to PNG images")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("output_dir")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--max-dim", type=int, default=1600)
    a = ap.parse_args()
    try:
        convert(a.pdf, a.output_dir, a.dpi, a.max_dim)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
