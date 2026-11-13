#!/usr/bin/env python3
"""Sync the project version into desktop manifests.

The Electron desktop shell (and its package.json files) has been removed, so
there is no manifest left to rewrite. The entry point is kept so existing
tooling that calls this script continues to work.
"""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def read_pyproject_version() -> str:
    pyproject = PROJECT_ROOT / "pyproject.toml"
    content = pyproject.read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if not m:
        print("ERROR: Cannot find version in pyproject.toml", file=sys.stderr)
        sys.exit(1)
    return m.group(1)


def main():
    version = read_pyproject_version()
    print(f"Nothing to sync: no package.json manifests depend on v{version}.")


if __name__ == "__main__":
    main()
