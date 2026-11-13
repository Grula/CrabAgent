#!/usr/bin/env python3
"""Inventory a .pptx: every slide's shapes, positions, text and fonts, plus layout warnings.

Usage:
    python pptx_inventory.py deck.pptx [--slide N] [--json] [--text-limit 80]

For each slide prints the layout name and every shape (group members included)
with its id, name, kind, placeholder type, box in inches (x, y, w, h), font sizes
found in its runs, and the start of its text. Speaker-notes presence is shown.

Warnings (also returned in --json):
  OFF-SLIDE     shape extends beyond the slide edge
  OVERLAP       two content shapes (text, chart, table, picture) overlap by more than
                15% of the smaller box (full-slide backgrounds are ignored)
  TIGHT-MARGIN  text shape closer than 0.3in to the slide edge
  PLACEHOLDER   leftover template text (lorem, xxxx, click to add, TODO, [insert ...])
  EMPTY         empty title/body placeholder left on the slide
  NO-ALT        picture without alternative text
  SMALL-FONT    explicit run size below 10pt
  DENSE         estimated text volume likely exceeds the box (rough heuristic)

Requires python-pptx (pip install python-pptx). The heuristics cannot see real
rendering - always confirm with a rendered image before declaring a deck done.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

EMU_PER_INCH = 914400
PLACEHOLDER_RE = re.compile(
    r"lorem|ipsum|\bx{3,}\b|click to (add|edit)|\bTODO\b|\bTBD\b|\[insert|<placeholder>|\{\{.*?\}\}",
    re.IGNORECASE,
)


def inches(emu) -> float:
    return round((emu or 0) / EMU_PER_INCH, 2)


def iter_shapes(shapes, depth=0):
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    for shape in shapes:
        yield shape, depth
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            yield from iter_shapes(shape.shapes, depth + 1)


def shape_kind(shape) -> str:
    if shape.is_placeholder:
        return "placeholder"
    if getattr(shape, "has_chart", False) and shape.has_chart:
        return "chart"
    if getattr(shape, "has_table", False) and shape.has_table:
        return "table"
    try:
        return str(shape.shape_type).split(".")[-1].split(" ")[0].lower()
    except Exception:  # noqa: BLE001
        return "shape"


def text_and_sizes(shape) -> tuple[str, list[float]]:
    texts, sizes = [], set()
    if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
        for para in shape.text_frame.paragraphs:
            texts.append("".join(run.text for run in para.runs))
            for run in para.runs:
                if run.font.size is not None:
                    sizes.add(round(run.font.size.pt, 1))
    elif getattr(shape, "has_table", False) and shape.has_table:
        for row in shape.table.rows:
            texts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(texts).strip(), sorted(sizes)


def overlap_ratio(a, b) -> float:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    w = min(ax1, bx1) - max(ax0, bx0)
    h = min(ay1, by1) - max(ay0, by0)
    if w <= 0 or h <= 0:
        return 0.0
    smaller = min((ax1 - ax0) * (ay1 - ay0), (bx1 - bx0) * (by1 - by0)) or 1
    return (w * h) / smaller


def inventory(path: str, only_slide: int | None, text_limit: int) -> dict:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    prs = Presentation(path)
    sw, sh = prs.slide_width, prs.slide_height
    result = {
        "file": path,
        "slide_size_in": [inches(sw), inches(sh)],
        "slides": [],
        "warning_count": 0,
    }
    for idx, slide in enumerate(prs.slides, start=1):
        if only_slide and idx != only_slide:
            continue
        entry = {
            "index": idx,
            "layout": slide.slide_layout.name,
            "has_notes": bool(slide.has_notes_slide and slide.notes_slide.notes_text_frame.text.strip()),
            "shapes": [],
            "warnings": [],
        }
        text_boxes = []
        for shape, depth in iter_shapes(slide.shapes):
            text, sizes = text_and_sizes(shape)
            box = None
            if shape.left is not None and shape.width is not None:
                box = (shape.left, shape.top, shape.left + shape.width, shape.top + shape.height)
            ph_type = None
            if shape.is_placeholder:
                try:
                    ph_type = str(shape.placeholder_format.type).split(".")[-1].split(" ")[0]
                except Exception:  # noqa: BLE001
                    ph_type = "unknown"
            info = {
                "id": shape.shape_id,
                "name": shape.name,
                "kind": shape_kind(shape),
                "placeholder": ph_type,
                "depth": depth,
                "box_in": [inches(shape.left), inches(shape.top), inches(shape.width), inches(shape.height)]
                if box else None,
                "font_pt": sizes,
                "text": text[:text_limit] + ("..." if len(text) > text_limit else ""),
            }
            entry["shapes"].append(info)
            label = f"shape {shape.shape_id} '{shape.name}'"

            if box and depth == 0:
                if box[0] < 0 or box[1] < 0 or box[2] > sw or box[3] > sh:
                    entry["warnings"].append(f"OFF-SLIDE {label} box={info['box_in']}")
                elif text and min(box[0], box[1], sw - box[2], sh - box[3]) < 0.3 * EMU_PER_INCH:
                    entry["warnings"].append(f"TIGHT-MARGIN {label}")
            if text and PLACEHOLDER_RE.search(text):
                entry["warnings"].append(f"PLACEHOLDER {label}: {text[:60]!r}")
            if shape.is_placeholder and not text and ph_type and any(
                k in ph_type for k in ("TITLE", "BODY", "SUBTITLE", "OBJECT")
            ):
                entry["warnings"].append(f"EMPTY {label} ({ph_type})")
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                descr = shape._element.xpath("./p:nvPicPr/p:cNvPr/@descr")
                if not descr or not descr[0].strip():
                    entry["warnings"].append(f"NO-ALT {label}")
            if sizes and min(sizes) < 10:
                entry["warnings"].append(f"SMALL-FONT {label} sizes={sizes}")
            if text and box and sizes:
                # Rough capacity estimate: average glyph width ~0.5em, line height ~1.2em.
                pt = max(sizes)
                width_pt = (box[2] - box[0]) / 12700
                height_pt = (box[3] - box[1]) / 12700
                chars_per_line = max(1.0, width_pt / (pt * 0.5))
                lines = sum(max(1, -(-len(line) // int(chars_per_line))) for line in text.split("\n"))
                if lines * pt * 1.2 > height_pt * 1.15:
                    entry["warnings"].append(
                        f"DENSE {label}: ~{lines} lines at {pt}pt need ~{lines * pt * 1.2 / 72:.2f}in, "
                        f"box is {height_pt / 72:.2f}in high"
                    )
            is_content = bool(text) or info["kind"] in ("chart", "table", "picture")
            if box and depth == 0 and is_content:
                area = (box[2] - box[0]) * (box[3] - box[1])
                if area < 0.6 * sw * sh:          # skip full-bleed backgrounds
                    text_boxes.append((label, box))

        for i in range(len(text_boxes)):
            for j in range(i + 1, len(text_boxes)):
                ratio = overlap_ratio(text_boxes[i][1], text_boxes[j][1])
                if ratio > 0.15:
                    entry["warnings"].append(
                        f"OVERLAP {text_boxes[i][0]} x {text_boxes[j][0]} ({ratio:.0%} of smaller box)"
                    )
        result["warning_count"] += len(entry["warnings"])
        result["slides"].append(entry)
    return result


def print_inventory(inv: dict) -> None:
    w, h = inv["slide_size_in"]
    print(f"{inv['file']}  slide size {w}in x {h}in  slides={len(inv['slides'])}")
    for s in inv["slides"]:
        print(f"\n=== Slide {s['index']}  layout='{s['layout']}'  notes={'yes' if s['has_notes'] else 'no'}")
        for sh in s["shapes"]:
            indent = "  " * (sh["depth"] + 1)
            box = sh["box_in"]
            box_s = f"x={box[0]} y={box[1]} w={box[2]} h={box[3]}" if box else "no-box"
            ph = f" ph={sh['placeholder']}" if sh["placeholder"] else ""
            fonts = f" pt={sh['font_pt']}" if sh["font_pt"] else ""
            text = sh["text"].replace("\n", " / ")
            print(f"{indent}[{sh['id']}] {sh['kind']}{ph} '{sh['name']}' {box_s}{fonts}"
                  + (f'  "{text}"' if text else ""))
        for warn in s["warnings"]:
            print(f"  ! {warn}")
    print(f"\nTotal warnings: {inv['warning_count']}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("deck")
    ap.add_argument("--slide", type=int)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--text-limit", type=int, default=80)
    args = ap.parse_args()
    try:
        import pptx  # noqa: F401
    except ImportError:
        print("ERROR: python-pptx is required (pip install python-pptx)", file=sys.stderr)
        return 2
    try:
        inv = inventory(args.deck, args.slide, args.text_limit)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(inv, indent=2))
    else:
        print_inventory(inv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
