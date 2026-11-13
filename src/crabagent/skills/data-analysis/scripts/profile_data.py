#!/usr/bin/env python3
"""Quick data-quality profile of a tabular file.

Usage:
    python profile_data.py data.csv [--sheet NAME] [--sep ","] [--key col1,col2]
                           [--date COL] [--freq D|W|M] [--top 5] [--json]

Reads .csv/.tsv/.txt, .xlsx/.xlsm/.xls, .parquet, .json/.jsonl (pandas).

Reports:
  * shape, memory, exact duplicate rows
  * per column: dtype, % missing, distinct count, sentinel values ('', 'n/a',
    'null', 'unknown', '-', -1, 9999 ...), numeric min/median/max, top values,
    whitespace / case variants that collapse to the same value, numbers or
    dates stored as text
  * candidate keys (single columns that are unique and complete) and, with
    --key, the duplicate count for that (composite) key
  * with --date: coverage (min/max), rows per period, empty periods in the range,
    future dates

The goal is to surface problems quickly; interpret every finding against the
intended grain and use of the data before calling it a defect.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SENTINEL_STRINGS = {"", "na", "n/a", "nan", "null", "none", "nil", "unknown", "-", "--", "?", "tbd", "#n/a"}
SENTINEL_NUMBERS = {-1, -99, -999, 999, 9999, 99999, -9999}


def load(path: Path, sheet: str | None, sep: str | None):
    import pandas as pd

    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv", ".txt"}:
        delimiter = sep or ("\t" if suffix == ".tsv" else None)
        return pd.read_csv(path, sep=delimiter, engine="python", dtype_backend="numpy_nullable")
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return pd.read_excel(path, sheet_name=sheet or 0)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in {".json", ".jsonl"}:
        return pd.read_json(path, lines=suffix == ".jsonl")
    raise ValueError(f"unsupported file type: {suffix}")


def column_profile(s, top: int) -> dict:
    import pandas as pd

    n = len(s)
    info: dict = {
        "dtype": str(s.dtype),
        "missing_pct": round(100 * s.isna().mean(), 2) if n else 0.0,
        "distinct": int(s.nunique(dropna=True)),
    }
    non_null = s.dropna()
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        if len(non_null):
            info.update(
                min=float(non_null.min()), median=float(non_null.median()), max=float(non_null.max()),
                zeros_pct=round(100 * (non_null == 0).mean(), 2),
                negative_pct=round(100 * (non_null < 0).mean(), 2),
            )
            sentinels = {int(v): int(c) for v, c in non_null[non_null.isin(SENTINEL_NUMBERS)].value_counts().items()}
            if sentinels:
                info["sentinel_values"] = sentinels
    elif pd.api.types.is_datetime64_any_dtype(s):
        if len(non_null):
            info.update(min=str(non_null.min()), max=str(non_null.max()))
    else:
        text = non_null.astype(str)
        lowered = text.str.strip().str.lower()
        sentinels = {k: int(v) for k, v in lowered[lowered.isin(SENTINEL_STRINGS)].value_counts().items()}
        if sentinels:
            info["sentinel_values"] = sentinels
        variants = int(text.nunique() - lowered.nunique())
        if variants > 0:
            info["case_or_whitespace_variants"] = variants
        if len(text):
            as_num = pd.to_numeric(text.str.replace(r"[,\s$%€£]", "", regex=True), errors="coerce")
            num_share = as_num.notna().mean()
            if num_share > 0.9:
                info["looks_numeric_text"] = round(100 * num_share, 1)
            elif num_share < 0.1:
                parsed = pd.to_datetime(text, errors="coerce", format="mixed")
                date_share = parsed.notna().mean()
                if date_share > 0.9:
                    info["looks_date_text"] = round(100 * date_share, 1)
                    info.update(min=str(parsed.min()), max=str(parsed.max()))
        lengths = text.str.len()
        if len(lengths):
            info["text_len_min_max"] = [int(lengths.min()), int(lengths.max())]
    if top and len(non_null):
        vc = non_null.astype(str).value_counts().head(top)
        info["top_values"] = {k: int(v) for k, v in vc.items()}
    return info


def date_coverage(df, col: str, freq: str) -> dict:
    import pandas as pd

    d = pd.to_datetime(df[col], errors="coerce", format="mixed")
    out = {
        "column": col,
        "unparseable": int(d.isna().sum() - df[col].isna().sum()),
        "missing": int(df[col].isna().sum()),
    }
    valid = d.dropna()
    if valid.empty:
        return out
    tz = getattr(valid.dt, "tz", None)
    now = pd.Timestamp.now(tz=tz)
    out.update(min=str(valid.min()), max=str(valid.max()), future_rows=int((valid > now).sum()))
    counts = valid.dt.to_period("M" if freq == "M" else freq).value_counts().sort_index()
    full = pd.period_range(valid.min(), valid.max(), freq="M" if freq == "M" else freq)
    empty = [str(p) for p in full if p not in counts.index]
    out["rows_per_period"] = {str(k): int(v) for k, v in counts.tail(12).items()}
    out["periods_total"] = len(full)
    out["empty_periods"] = empty[:20] + (["..."] if len(empty) > 20 else [])
    if len(counts) >= 4:
        last, median = counts.iloc[-1], counts.iloc[:-1].median()
        if median and last < 0.5 * median:
            out["last_period_warning"] = (f"last period has {int(last)} rows vs median {median:.0f} - "
                                          "partial period or late-arriving data?")
    return out


def profile(df, key: list[str] | None, date_col: str | None, freq: str, top: int) -> dict:
    rep: dict = {
        "rows": int(len(df)),
        "columns": int(df.shape[1]),
        "memory_mb": round(df.memory_usage(deep=True).sum() / 1e6, 2),
        "duplicate_rows": int(df.duplicated().sum()),
        "empty_columns": [c for c in df.columns if df[c].isna().all()],
        "constant_columns": [c for c in df.columns if df[c].nunique(dropna=False) == 1],
        "column_profiles": {str(c): column_profile(df[c], top) for c in df.columns},
    }
    rep["candidate_keys"] = [
        str(c) for c in df.columns if len(df) and df[c].notna().all() and df[c].nunique() == len(df)
    ]
    if key:
        missing = [k for k in key if k not in df.columns]
        if missing:
            rep["key_check"] = {"error": f"columns not found: {missing}"}
        else:
            dup_mask = df.duplicated(subset=key, keep=False)
            rep["key_check"] = {
                "key": key,
                "rows_in_duplicate_groups": int(dup_mask.sum()),
                "duplicate_keys": int(df[dup_mask].drop_duplicates(subset=key).shape[0]),
                "null_key_rows": int(df[key].isna().any(axis=1).sum()),
                "examples": df[dup_mask].head(5).astype(str).to_dict("records"),
            }
    if date_col:
        rep["date_coverage"] = (date_coverage(df, date_col, freq) if date_col in df.columns
                                else {"error": f"column not found: {date_col}"})
    return rep


def print_report(rep: dict, path: Path) -> None:
    print(f"File: {path}")
    print(f"Rows: {rep['rows']:,}   Columns: {rep['columns']}   Memory: {rep['memory_mb']} MB   "
          f"Exact duplicate rows: {rep['duplicate_rows']:,}")
    if rep["empty_columns"]:
        print(f"Empty columns: {rep['empty_columns']}")
    if rep["constant_columns"]:
        print(f"Constant columns: {rep['constant_columns']}")
    print(f"Candidate keys (unique + complete): {rep['candidate_keys'] or 'none'}")
    print("\nColumns:")
    for name, p in rep["column_profiles"].items():
        line = f"  {name:28.28} {p['dtype']:14.14} missing={p['missing_pct']:>6}%  distinct={p['distinct']:<8}"
        if "min" in p:
            mid = f" median={p['median']:.4g}" if "median" in p else ""
            line += f" min={p['min']}{mid} max={p['max']}"
        print(line)
        notes = []
        for k in ("sentinel_values", "case_or_whitespace_variants", "looks_numeric_text", "looks_date_text",
                  "negative_pct"):
            if p.get(k):
                notes.append(f"{k}={p[k]}")
        if notes:
            print(f"      ! {'; '.join(notes)}")
        if p.get("top_values"):
            tops = ", ".join(f"{k[:20]}({v})" for k, v in p["top_values"].items())
            print(f"      top: {tops}")
    if "key_check" in rep:
        print(f"\nKey check: {json.dumps(rep['key_check'], default=str)[:800]}")
    if "date_coverage" in rep:
        print(f"\nDate coverage: {json.dumps(rep['date_coverage'], default=str, indent=2)[:1500]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--sheet")
    ap.add_argument("--sep")
    ap.add_argument("--key", help="comma-separated column(s) expected to be unique")
    ap.add_argument("--date", help="date/timestamp column for coverage checks")
    ap.add_argument("--freq", choices=["D", "W", "M"], default="D")
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    try:
        import pandas  # noqa: F401
    except ImportError:
        print("ERROR: pandas is required (pip install pandas openpyxl)", file=sys.stderr)
        return 2
    path = Path(args.path).expanduser()
    try:
        df = load(path, args.sheet, args.sep)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: could not read {path}: {exc}", file=sys.stderr)
        return 2
    key = [k.strip() for k in args.key.split(",")] if args.key else None
    rep = profile(df, key, args.date, args.freq, args.top)
    if args.json:
        print(json.dumps(rep, indent=2, default=str))
    else:
        print_report(rep, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
