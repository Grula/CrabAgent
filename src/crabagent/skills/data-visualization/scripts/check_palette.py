#!/usr/bin/env python3
"""Check a chart palette for contrast and colour-vision-deficiency (CVD) separation.

Usage:
    python check_palette.py "#0072B2,#E69F00,#009E73,#CC79A7" [--background "#FFFFFF"]
                            [--pairs adjacent|all] [--json]

Pure Python, no dependencies.

Checks, per colour / pair:
  contrast   WCAG 2.x contrast ratio of each colour against the background.
             >= 3.0 is the target for chart marks (lines, bars, dots); below it
             the values must also be readable another way (labels / table).
  distance   Perceptual distance between colours (Euclidean distance in OKLab,
             x100) for normal vision and simulated protanopia, deuteranopia and
             tritanopia (Machado, Oliveira & Fernandes 2009, severity 1.0).
  lightness  Spread of OKLab lightness - how well the palette survives grayscale
             printing.

Rating of the worst pair (simulated vision):
  >= 12  GOOD      colour alone separates the series
  7-12   MARGINAL  add a redundant cue: direct labels, markers, dash styles
  < 7    POOR      reorder, swap a hue, or reduce the number of series

`--pairs adjacent` (default) compares colours that sit next to each other in the
given order (stacked bars, lines, legends). Use `--pairs all` for scatter plots,
maps and small multiples, where any two colours can end up side by side.

Exit code: 0 when every pair is at least MARGINAL and no colour is below 2:1
contrast, 1 otherwise, 2 on bad input.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys

# Machado, Oliveira & Fernandes (2009) simulation matrices, severity 1.0, linear RGB.
CVD_MATRICES = {
    "protan": ((0.152286, 1.052583, -0.204868), (0.114503, 0.786281, 0.099216), (-0.003882, -0.048116, 1.051998)),
    "deutan": ((0.367322, 0.860646, -0.227968), (0.280085, 0.672501, 0.047413), (-0.011820, 0.042940, 0.968881)),
    "tritan": ((1.255528, -0.076749, -0.178779), (-0.078411, 0.930809, 0.147602), (0.004733, 0.691367, 0.303900)),
}


def parse_hex(value: str) -> tuple[float, float, float]:
    h = value.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(ch * 2 for ch in h)
    if len(h) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in h):
        raise ValueError(f"not a hex colour: {value!r}")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(rgb) -> float:
    r, g, b = (to_linear(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = rel_luminance(a), rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def linear_to_oklab(lin) -> tuple[float, float, float]:
    r, g, b = lin
    l_ = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m_ = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s_ = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = (math.copysign(abs(v) ** (1 / 3), v) for v in (l_, m_, s_))
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def simulate(lin, matrix):
    out = []
    for row in matrix:
        v = sum(row[i] * lin[i] for i in range(3))
        out.append(min(1.0, max(0.0, v)))
    return tuple(out)


def oklab_for(rgb, mode: str):
    lin = tuple(to_linear(c) for c in rgb)
    if mode != "normal":
        lin = simulate(lin, CVD_MATRICES[mode])
    return linear_to_oklab(lin)


def distance(a, b) -> float:
    return 100 * math.dist(a, b)


def rating(d: float) -> str:
    if d >= 12:
        return "GOOD"
    if d >= 7:
        return "MARGINAL"
    return "POOR"


def analyse(colors: list[str], background: str, pairs: str) -> dict:
    rgbs = [parse_hex(c) for c in colors]
    bg = parse_hex(background)
    idx_pairs = (
        list(itertools.combinations(range(len(rgbs)), 2)) if pairs == "all"
        else [(i, i + 1) for i in range(len(rgbs) - 1)]
    )
    report = {"background": background, "pairs_mode": pairs, "colors": [], "worst": {}, "pairs": []}
    for c, rgb in zip(colors, rgbs):
        ratio = contrast(rgb, bg)
        report["colors"].append({
            "color": c,
            "contrast": round(ratio, 2),
            "contrast_ok": ratio >= 3.0,
            "lightness": round(oklab_for(rgb, "normal")[0], 3),
        })
    for mode in ("normal", "protan", "deutan", "tritan"):
        labs = [oklab_for(rgb, mode) for rgb in rgbs]
        worst = None
        for i, j in idx_pairs:
            d = distance(labs[i], labs[j])
            report["pairs"].append({"mode": mode, "a": colors[i], "b": colors[j], "delta": round(d, 1)})
            if worst is None or d < worst[0]:
                worst = (d, colors[i], colors[j])
        if worst:
            report["worst"][mode] = {"delta": round(worst[0], 1), "a": worst[1], "b": worst[2],
                                     "rating": rating(worst[0])}
    ls = [c["lightness"] for c in report["colors"]]
    report["lightness_spread"] = round(max(ls) - min(ls), 3) if ls else 0
    simulated = [v for k, v in report["worst"].items() if k != "normal"]
    report["passed"] = (
        all(v["rating"] != "POOR" for v in report["worst"].values())
        and all(c["contrast"] >= 2.0 for c in report["colors"])
    )
    report["needs_redundant_encoding"] = any(v["rating"] == "MARGINAL" for v in simulated) or any(
        not c["contrast_ok"] for c in report["colors"])
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("colors", help="comma-separated hex colours in assignment order")
    ap.add_argument("--background", default="#FFFFFF")
    ap.add_argument("--pairs", choices=["adjacent", "all"], default="adjacent")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    colors = [c.strip() for c in args.colors.split(",") if c.strip()]
    if len(colors) < 1:
        print("ERROR: give at least one colour", file=sys.stderr)
        return 2
    try:
        rep = analyse(colors, args.background, args.pairs)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(rep, indent=2))
        return 0 if rep["passed"] else 1

    print(f"Background {rep['background']}   pairs={rep['pairs_mode']}")
    print(f"{'colour':10} {'contrast':>9}  {'L':>5}")
    for c in rep["colors"]:
        flag = "" if c["contrast_ok"] else ("  < 3:1 -> label values or add a table" if c["contrast"] >= 2
                                            else "  < 2:1 -> too faint on this background")
        print(f"{c['color']:10} {c['contrast']:>8.2f}:1 {c['lightness']:>5}{flag}")
    if rep["worst"]:
        print("\nWorst pair per vision type (OKLab distance x100):")
        for mode, w in rep["worst"].items():
            print(f"  {mode:7} {w['delta']:>5}  {w['a']} vs {w['b']}  {w['rating']}")
    print(f"\nLightness spread: {rep['lightness_spread']} "
          f"({'survives grayscale' if rep['lightness_spread'] >= 0.25 else 'weak in grayscale - add markers/patterns'})")
    verdict = "PASS" if rep["passed"] else "FAIL"
    extra = " (use direct labels / markers as a redundant cue)" if rep["needs_redundant_encoding"] else ""
    print(f"Verdict: {verdict}{extra}")
    return 0 if rep["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
