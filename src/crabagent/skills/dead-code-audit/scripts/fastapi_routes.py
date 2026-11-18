"""List FastAPI routes that no client code appears to call. Read-only.

usage: fastapi_routes.py --app pkg.module:app_or_factory --client <dir> [<dir> ...] [--prefix /api]

Imports the app (a FastAPI instance, or a zero-argument factory), reads its
OpenAPI paths and searches the client directories for each path, with path
parameters allowed to be any segment or a template expression (${id}, {id}).

  NO MATCH    nothing in the clients looks like this path
  TAIL ONLY   only the end of the path matched (often a URL built from a helper,
              e.g. `${base(session)}/groups`): check by hand

A path can be live for one method and dead for another (POST used, DELETE not),
and a URL can come from the server itself (a redirect, a link it returns): this
script can't see either, so check those by hand.

Run it with the project's own environment, e.g. PYTHONPATH=src python3 ...
Importing the app must not start a server or write data; point any database
setting at a throwaway path if the import touches it.
"""
from __future__ import annotations

import argparse
import importlib
import re
from pathlib import Path

CLIENT_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".html", ".vue", ".svelte", ".py", ".sh", ".md"}
PARAM = r"""(?:\$\{[^}]*\}|\{[^}]*\}|[^/'"`\s?]+)"""


def load_app(spec: str):
    mod_name, _, attr = spec.partition(":")
    obj = getattr(importlib.import_module(mod_name), attr or "app")
    return obj() if callable(obj) and not hasattr(obj, "openapi") else obj


def path_regex(parts: list[str]) -> re.Pattern:
    body = "/".join(PARAM if p.startswith("{") else re.escape(p) for p in parts)
    # the path may be followed by a quote, query, slash, or a template expression (${qs})
    return re.compile(r"/" + body + r"""(?:['"`?/\s#$]|$)""")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--client", nargs="+", type=Path, required=True)
    ap.add_argument("--prefix", default="", help="mount prefix the clients leave out, e.g. /api")
    args = ap.parse_args()

    texts = []
    for d in args.client:
        files = [d] if d.is_file() else [f for f in d.rglob("*") if f.is_file() and f.suffix in CLIENT_EXTS
                                         and "node_modules" not in f.parts and ".test." not in f.name]
        texts.extend((f, f.read_text(encoding="utf-8", errors="ignore")) for f in files)

    app = load_app(args.app)
    rows = []
    for path, ops in app.openapi()["paths"].items():
        rel = path[len(args.prefix):] if args.prefix and path.startswith(args.prefix) else path
        parts = [p for p in rel.split("/") if p]
        if not parts:
            continue
        methods = ",".join(sorted(m.upper() for m in ops))
        names = ";".join(o.get("operationId", "?").split("_" + parts[0])[0] for o in ops.values())
        if any(path_regex(parts).search(t) for _, t in texts):
            continue
        # shortest ending with a literal segment: URLs built from a helper keep only that much
        tail = parts[-1:] if not parts[-1].startswith("{") else parts[-2:]
        tail_hits = [str(f) for f, t in texts if path_regex(tail).search(t)] if any(not p.startswith("{") for p in tail) else []
        status = "TAIL ONLY" if tail_hits else "NO MATCH "
        rows.append(f"{status} {methods:16} {path}  [{names}]" + (f"  tail in: {', '.join(tail_hits[:3])}" if tail_hits else ""))
    for r in sorted(rows):
        print(r)
    print(f"\n{len(rows)} of {len(app.openapi()['paths'])} paths without a full match")


if __name__ == "__main__":
    main()
