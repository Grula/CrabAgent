"""code_search: ranked symbol and text search over the workspace code index.

The index itself lives in :mod:`crabagent.core.code_index` (FTS5 over extracted
symbols plus 60-line text chunks). This module is the agent-facing half: it
fetches candidates, ranks them, and renders compact ``path:lines`` hits so a
result costs tens of tokens instead of a whole file.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from crabagent.core.agent.tools.path_utils import get_workspace_root
from crabagent.core.agent.tools.registry import registry
from crabagent.core.code_index import Match, ensure_indexed, iter_matches
from crabagent.core.code_index.extract import SYMBOL_KINDS
from crabagent.core.code_index.lsp import navigator_for
from crabagent.core.code_index.references import (
    Reference,
    _qualifier_words,
    find_references,
    format_references,
    narrow_to_qualifier,
    python_definitions,
)
from crabagent.core.code_index.references import sort_key as reference_sort_key

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
# Candidates fetched per FTS table and per pass, so ranking has room to reorder.
CANDIDATE_FACTOR = 5
MIN_CANDIDATES = 50
# One big file must not fill the list.
PER_FILE_CAP = 3
MAX_LINE_CHARS = 200
MAX_DETAIL_CHARS = 100
MAX_SNIPPET_CHARS = 160
MAX_EXCERPT_LINES = 2

_DESCRIPTION = (
    "Search the workspace's code index for symbols (functions, methods, classes, headings) "
    "and file text; exact and prefix name matches rank first. Use kind=\"symbol\" to find a "
    "definition by name, and kind=\"reference\" with one name to list its definitions, calls, "
    "imports and other uses (who calls X). Prefer grep for exact literal text, regexes, or paraphrastic queries "
    "this index may miss. Pass reindex=true after large changes."
)


# ── ranking ────────────────────────────────────────────────────────────────


def _name_parts(name: str) -> list[str]:
    """``a.b.c`` -> ``["a.b.c", "b.c", "c"]`` (lowercased, so matching is case-insensitive)."""
    lowered = (name or "").lower()
    if not lowered:
        return []
    segments = lowered.split(".")
    return [".".join(segments[index:]) for index in range(len(segments))]


# Punctuation a model wraps an identifier in ("ToolRegistry.execute("), which
# FTS drops but a plain string comparison would not.
_IDENT_EDGES = "\"'`()[]{}<>,;:+-*/&|!?="


def _match_tokens(tokens: Sequence[str]) -> list[str]:
    """Tokens trimmed of surrounding punctuation, for name comparisons only."""
    cleaned: list[str] = []
    for token in tokens:
        stripped = token.strip(_IDENT_EDGES)
        if stripped:
            cleaned.append(stripped)
    return cleaned


# Query words are split the way FTS (unicode61) splits them: on punctuation and
# underscores, but not on camelCase boundaries. ``\W`` is Unicode-aware, so a
# word like ``číst`` stays one word; the old ASCII-only class cut it into
# ``["st"]`` and the exact tier was lost for any non-ASCII query word.
_QUERY_SPLIT = re.compile(r"[\W_]+", re.UNICODE)
# Names are split further, on camelCase boundaries too, so "ToolRegistry" and
# "toolregistry" are the same name and "workspace_tree" is two words.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _query_words(tokens: Sequence[str]) -> list[str]:
    """Distinct lowercase query words, tokenised like FTS.

    ``ToolRegistry.execute`` -> ``["toolregistry", "execute"]``; one word per
    camelCase name, because the FTS tokenizer does not split camelCase (that is
    why :func:`_name_forms` splits the name side instead).
    """
    words: list[str] = []
    for token in tokens:
        for word in _QUERY_SPLIT.split(token.lower()):
            if word and word not in words:
                words.append(word)
    return words


def _split_words(segment: str) -> list[str]:
    """Lowercase words of one dotted segment: ``Workspace_Tree`` -> ``[workspace, tree]``."""
    words: list[str] = []
    for chunk in segment.split("_"):
        for word in _CAMEL_BOUNDARY.split(chunk):
            if word:
                words.append(word.lower())
    return words


def _name_forms(*identifiers: str) -> set[str]:
    """Every spelling a query word may use to cover these identifiers.

    Per dotted suffix (``ToolRegistry.execute``, ``execute``): the suffix itself,
    its camelCase/underscore words (``tool``, ``registry``, ``execute``) and every
    contiguous join of those words (``toolregistry``, ``registryexecute``). That
    way ``toolregistry`` covers ``ToolRegistry.execute`` while ``workspace`` covers
    only one of the two words in ``build_workspace_tree``.
    """
    forms: set[str] = set()
    for identifier in identifiers:
        segments = [seg for seg in (identifier or "").lower().split(".") if seg]
        for index in range(len(segments)):
            forms.add(".".join(segments[index:]))
            words = _split_words(segments[index])
            forms.update(words)
            for start in range(len(words)):
                joined = ""
                for word in words[start:]:
                    joined += word
                    forms.add(joined)
    return forms


def _covers(forms: set[str], query_words: Sequence[str], *, prefix: bool) -> bool:
    """Does the symbol name cover a strict majority of the query words?

    ``matched / |query_words| > 0.5`` — strictly greater, not ``>=``. With "at
    least half", ``workspace`` would cover 1 of the 2 words in ``workspace tree``
    and stay "exact", and so would ``diff`` for ``diff preview``: exactly the two
    results this rule exists to fix. A single-word query is always covered (that is
    the original single-token exact case).
    """
    if len(query_words) <= 1:
        return True
    matched = sum(
        1
        for word in query_words
        if word in forms or (prefix and any(form.startswith(word) for form in forms))
    )
    return matched / len(query_words) > 0.5


def _name_tier(match: Match, tokens: Sequence[str]) -> int:
    """Ranking tier of one hit: 0 exact name/qualname, 1 prefix, 2 everything else.

    Only symbol hits have names; a text chunk is always tier 2. A query token
    counts as an exact name match when it equals the symbol's ``name`` or
    ``qualname`` or one of its dotted suffixes (``execute`` matches
    ``ToolRegistry.execute``). The same suffix parts are compared as prefixes,
    which is what makes ``zebra`` reach ``ZebraCombinator`` — the FTS tokenizer
    does not split camelCase.

    Both tiers are gated on :func:`_covers`: a name that matches one word of a
    multi-word query is not "exact" for that query. The prefix tier is gated too,
    otherwise the loser of a tightened exact tier would simply reappear there.
    """
    if match.source != "symbol":
        return 2
    parts = _name_parts(match.name) + _name_parts(match.qualname)
    if not parts:
        return 2
    forms = _name_forms(match.name, match.qualname)
    words = _query_words(tokens)
    if _covers(forms, words, prefix=False) and any(token in parts for token in tokens):
        return 0
    if _covers(forms, words, prefix=True) and any(
        form.startswith(token) for form in forms for token in tokens
    ):
        return 1
    return 2


def _dedupe(hits: Iterable[Match]) -> list[Match]:
    """Drop repeat hits for the same symbol (or the same chunk) across passes."""
    seen: set[tuple] = set()
    unique: list[Match] = []
    for match in hits:
        key = (
            match.path,
            match.start_line,
            match.end_line,
            match.kind,
            match.name,
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(match)
    return unique


def _path_depth(path: str) -> int:
    return path.count("/")


def _sort_key(match: Match, tokens: Sequence[str]) -> tuple:
    """Sort key realizing the tier -> bm25 -> depth -> path order."""
    rank_score = -match.score  # bm25 is lower-is-better; higher rank_score is better
    return (
        _name_tier(match, tokens),
        -rank_score,
        _path_depth(match.path),
        match.path,
        match.start_line,
    )


def rank_matches(
    hits: Iterable[Match], tokens: Sequence[str], *, limit: int = DEFAULT_LIMIT
) -> tuple[list[Match], int]:
    """Order deduplicated hits and cap them; returns ``(shown, total)``.

    Ordering, deterministic and in this order:

    1. **exact** case-insensitive match of a query token against the symbol's
       ``name``/``qualname`` (or a dotted suffix of it) — tier 0. This is what
       makes ``kind="symbol"`` the "find definition" path with no extra
       parameter. Surrounding punctuation is ignored, so ``execute(`` still
       matches ``execute``. The tier only applies when the name covers a strict
       majority of the query words (:func:`_covers`), so ``workspace tree`` no
       longer ranks the function ``workspace`` first;
    2. **prefix** match on the same parts — tier 1 (catches ``Zebra`` ->
       ``ZebraCombinator``), gated on the same coverage rule;
    3. tier 2, the plain FTS hits. Inside every tier, better `bm25` first.
       SQLite's ``bm25()`` is lower-is-better, so the score stored on
       :class:`Match` is negative; we negate it into ``rank_score`` (higher is
       better) and sort descending;
    4. ties break on shallower path depth, then lexicographic path, then start
       line, so a ``src/`` hit beats a vendored copy and repeated runs agree.

    After ordering: one hit per symbol/chunk location, at most
    :data:`PER_FILE_CAP` hits per file, then ``limit`` hits returned. ``total``
    is the number of hits left after the per-file cap, i.e. what the caller
    reports as "… N of total shown".
    """
    match_tokens = _match_tokens(tokens)
    ranked = sorted(_dedupe(hits), key=lambda match: _sort_key(match, match_tokens))
    per_file: dict[str, int] = {}
    kept: list[Match] = []
    for match in ranked:
        used = per_file.get(match.path, 0)
        if used >= PER_FILE_CAP:
            continue
        per_file[match.path] = used + 1
        kept.append(match)
    return kept[: max(0, limit)], len(kept)


# ── kind filter ────────────────────────────────────────────────────────────


def _kind_filter(kind: str | None) -> tuple[tuple[str, ...] | None, bool, bool]:
    """Map the ``kind`` argument to ``(symbol_kinds, want_symbols, want_text)``.

    ``None``/empty means everything; ``"symbol"`` means every symbol kind and no
    text; ``"text"`` means chunks only; a specific symbol kind restricts symbols
    to it. Raises :class:`ValueError` for anything else.
    """
    normalised = (kind or "").strip().lower()
    if not normalised:
        return None, True, True
    if normalised == "symbol":
        return tuple(sorted(SYMBOL_KINDS)), True, False
    if normalised == "text":
        return None, False, True
    if normalised in SYMBOL_KINDS:
        return (normalised,), True, False
    raise ValueError(normalised)


# ── rendering ──────────────────────────────────────────────────────────────


def _clip(text: str, limit: int) -> str:
    """Collapse whitespace and truncate to ``limit`` characters with an ellipsis."""
    compact = " ".join((text or "").split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 1)].rstrip() + "…"


def _excerpt_lines(snippet: str) -> list[str]:
    """First couple of lines of a chunk snippet, each clipped."""
    lines = [line.strip() for line in (snippet or "").splitlines() if line.strip()]
    return [_clip(line, MAX_SNIPPET_CHARS) for line in lines[:MAX_EXCERPT_LINES]]


def format_hits(shown: Sequence[Match], total: int, query: str) -> str:
    """Render ranked hits as compact text, with a footer only when truncated."""
    if not shown:
        return f"No matches for '{query}' — try grep for literal text or a shorter query."
    out: list[str] = []
    for match in shown:
        location = f"{match.path}:{match.start_line}-{match.end_line}"
        if match.source == "symbol":
            label = f"{match.kind} {match.qualname or match.name}".strip()
            detail = _clip(match.doc or match.signature, MAX_DETAIL_CHARS)
            line = f"{location}  {label}"
            if detail:
                line += f'  — "{detail}"'
            out.append(_clip(line, MAX_LINE_CHARS))
            signature = _clip(match.signature, MAX_SNIPPET_CHARS)
            if signature and signature != detail:
                out.append("  " + signature)
        else:
            out.append(f"{location}  text")
            for excerpt in _excerpt_lines(match.snippet):
                out.append("  " + excerpt)
    if total > len(shown):
        out.append(f"… {len(shown)} of {total} shown")
    return "\n".join(out)


# ── tool ───────────────────────────────────────────────────────────────────


@registry.register(
    name="code_search",
    description=_DESCRIPTION,
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Words to search for; identifiers work best "
                    '(e.g. "ToolRegistry.execute", "process group").'
                ),
            },
            "path_prefix": {
                "type": "string",
                "description": (
                    'Only workspace-relative paths starting with this, e.g. "src/" or "tests/". '
                    "Default: the whole workspace."
                ),
            },
            "kind": {
                "type": "string",
                "description": (
                    'Filter: "symbol" (any symbol kind), "text" (file bodies only), "reference" '
                    "(every use of one name: definitions, calls, imports), one of "
                    "function, method, class, constant, interface, type, enum, heading, "
                    "or omit for everything."
                ),
            },
            "limit": {
                "type": "integer",
                "description": f"Maximum hits to show. Default {DEFAULT_LIMIT}, max {MAX_LIMIT}.",
            },
            "reindex": {
                "type": "boolean",
                "description": "Force a full index rebuild before searching. Default: false.",
            },
        },
        "required": ["query"],
    },
    requires_permission=False,
    metadata={"source": "builtin", "category": "search"},
)
async def code_search(
    query: str,
    path_prefix: str | None = None,
    kind: str | None = None,
    limit: int = DEFAULT_LIMIT,
    reindex: bool = False,
    context: Any = None,
) -> str:
    """Search the workspace's symbol and text index and return ranked locations."""
    workspace = get_workspace_root(context)
    if workspace is None:
        return "Error: no workspace is set for this session, so there is nothing to search."
    if (kind or "").strip().lower() == "reference":
        return await _references(workspace, query, path_prefix, limit)
    tokens = [token.lower() for token in (query or "").split() if token.strip()]
    if not tokens:
        return f"No matches for '{query}' — try grep for literal text or a shorter query."
    try:
        symbol_kinds, want_symbols, want_text = _kind_filter(kind)
    except ValueError:
        accepted = ", ".join(["symbol", "text", "reference", *sorted(SYMBOL_KINDS)])
        return f"Error: unknown kind '{kind}'; use one of {accepted}."
    if isinstance(limit, bool) or not isinstance(limit, int):
        limit = DEFAULT_LIMIT
    limit = min(max(limit, 1), MAX_LIMIT)

    try:
        await ensure_indexed(workspace, force=reindex)
    except FileNotFoundError as exc:
        return f"Error: {exc}"

    candidates = max(MIN_CANDIDATES, limit * CANDIDATE_FACTOR)
    hits: list[Match] = []
    for prefix in (False, True):  # prefix pass finds camelCase prefixes too
        found = await iter_matches(
            workspace,
            tokens,
            kinds=symbol_kinds,
            path_prefix=path_prefix,
            prefix=prefix,
            limit=candidates,
        )
        hits.extend(
            match
            for match in found
            if (match.source == "symbol" and want_symbols)
            or (match.source == "text" and want_text)
        )
    shown, total = rank_matches(hits, tokens, limit=limit)
    return format_hits(shown, total, query)


_IDENTIFIER = re.compile(r"[A-Za-z_$][\w$]*")
REFERENCE_DEFAULT_LIMIT = 30


async def _references(workspace, query: str, path_prefix: str | None, limit: Any) -> str:
    """kind="reference": every use of one name (the last part of a dotted name)."""
    names = _IDENTIFIER.findall((query or "").split()[0]) if (query or "").split() else []
    if not names:
        return "Error: kind=\"reference\" needs one identifier, e.g. \"spawn_sub_agent\" or \"ToolRegistry.execute\"."
    name = names[-1]
    qualifier = names[-2] if len(names) > 1 else ""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit == DEFAULT_LIMIT:
        limit = REFERENCE_DEFAULT_LIMIT
    limit = min(max(limit, 1), MAX_LIMIT)
    root = Path(workspace)
    refs = await asyncio.to_thread(find_references, root, name, path_prefix)
    precise, note = await _precise_python_references(root, name, qualifier, refs, path_prefix)
    if precise is not None:
        return format_references(precise, name, limit, note=note)
    hidden = 0
    if qualifier:
        refs, hidden = narrow_to_qualifier(refs, qualifier)
    return format_references(refs, name, limit, hidden, qualifier, note=note)


async def _precise_python_references(
    root: Path, name: str, qualifier: str, by_name: list, path_prefix: str | None
) -> tuple[list | None, str]:
    """References from the Python language server when the name has exactly one Python definition.

    Returns ``(references, note)``; ``references`` is None when name matching has to do
    (no or several definitions, no server), and ``note`` then says why.
    """
    definitions = await asyncio.to_thread(python_definitions, root, name, qualifier)
    if not definitions:
        return None, ""
    if len(definitions) > 1:
        names = ", ".join(sorted({d.qualname for d in definitions})[:6])
        return None, (
            f"[by name: {len(definitions)} Python definitions are called '{name}' ({names}); "
            f"search one as Class.{name} for precise results]"
        )
    navigator = navigator_for(root)
    if navigator is None:
        return None, "[by name: install basedpyright for precise Python results]"
    target = definitions[0]
    locations = await navigator.references(root / target.path, target.line, target.column)
    if not locations:
        return None, "[by name: the Python language server did not answer]"
    kinds = {(r.path, r.line): r for r in by_name}
    prefix = (path_prefix or "").strip().removeprefix("./")
    lines_of: dict[Path, list[str]] = {}
    precise = []
    for location in locations:
        try:
            rel = location.path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            continue  # site-packages and other code outside the workspace
        if prefix and not rel.startswith(prefix):
            continue
        known = kinds.get((rel, location.line))
        if known is not None:
            precise.append(known)
            continue
        if location.path not in lines_of:
            lines_of[location.path] = await asyncio.to_thread(_read_lines, location.path)
        lines = lines_of[location.path]
        text = lines[location.line - 1] if 0 < location.line <= len(lines) else ""
        kind = "definition" if (rel, location.line) == (target.path, target.line) else "reference"
        precise.append(Reference(rel, location.line, kind, " ".join(text.split())[:160]))
    unique = {(r.path, r.line): r for r in precise}
    # The server can't see through untyped code (`def run(registry): registry.execute()`):
    # keep name matches it didn't confirm, marked, so precision doesn't cost recall.
    if qualifier:
        # Strict here: only receivers named like the class (narrow_to_qualifier keeps
        # everything when none is, which would put db.execute back).
        words = _qualifier_words(qualifier)
        candidates = [r for r in by_name if r.receiver and any(w in r.receiver.lower() for w in words)]
    else:
        candidates = by_name
    extra = [
        Reference(r.path, r.line, r.kind, f"(by name) {r.text}", r.receiver)
        for r in candidates
        if (r.path, r.line) not in unique and r.kind != "definition"
        and (not prefix or r.path.startswith(prefix))
    ]
    note = f"[precise: {target.qualname}, via basedpyright"
    if extra:
        note += f"; {len(extra)} more matched by name only (types unknown), marked (by name)"
    merged = sorted([*unique.values(), *extra], key=reference_sort_key)
    return merged, note + "]"


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
