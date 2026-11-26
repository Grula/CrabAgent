from crabagent.core.agent.tools.diagnostics import post_edit_report
from crabagent.core.agent.tools.file_state import tracker_of
from crabagent.core.agent.tools.path_utils import file_lock, resolve_tool_path
from crabagent.core.agent.tools.registry import registry

_MAX_SNIPPET_CHARS = 60


def _snippet(text: str) -> str:
    """One-line, length-capped rendering of an old_string for a result line."""
    flat = text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    if len(flat) > _MAX_SNIPPET_CHARS:
        flat = flat[:_MAX_SNIPPET_CHARS]
    return flat


def _apply_items(content: str, items: list) -> tuple[str, list[dict]]:
    """Apply replacement items sequentially against *content* in memory.

    Later items see the effect of earlier ones. Nothing is written here; the
    caller writes the returned text once. Per-item outcomes are returned in
    order so the caller can report them individually.
    """
    outcomes: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            outcomes.append({"ok": False, "reason": "invalid item (expected an object)"})
            continue
        old_string = item.get("old_string")
        new_string = item.get("new_string")
        replace_all = bool(item.get("replace_all", False))
        if not isinstance(old_string, str) or not old_string:
            outcomes.append({"ok": False, "reason": "empty old_string"})
            continue
        if not isinstance(new_string, str):
            outcomes.append({"ok": False, "reason": "missing new_string"})
            continue
        if old_string == new_string:
            outcomes.append({"ok": False, "reason": "identical old_string and new_string"})
            continue

        count = content.count(old_string)
        if count == 0:
            outcomes.append({"ok": False, "reason": f"not found: {_snippet(old_string)}"})
            continue
        if count > 1 and not replace_all:
            outcomes.append({"ok": False, "reason": f"ambiguous: {count} matches"})
            continue

        index = content.index(old_string)
        line_num = content[:index].count("\n") + 1
        if replace_all:
            content = content.replace(old_string, new_string)
        else:
            content = content.replace(old_string, new_string, 1)
        outcomes.append({"ok": True, "count": count, "line": line_num})
    return content, outcomes


def _batch_ok(outcomes: list[dict]) -> bool:
    """True when every item of a replacements batch applied.

    A replacements batch is all-or-nothing, so this is the single rule both the
    tool (before writing) and the approval-card preview (before showing a diff)
    use to decide whether the batch as a whole would succeed.
    """
    return all(outcome["ok"] for outcome in outcomes)


def _detect_newline(raw: bytes) -> str:
    """The file's own line ending, decided from its bytes (LF when there is none)."""
    crlf = raw.count(b"\r\n")
    lf = raw.count(b"\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _read_preserving(path) -> tuple[str, str]:
    """Read *path* as LF text plus the newline to write back.

    ``Path.read_text`` uses universal-newline mode, so a CRLF file comes back as
    LF and used to be written back as LF. Reading the bytes and normalising to
    LF here lets :func:`_write_edited` restore the file's own line endings, so
    an edit changes only the text the caller asked about. The trailing-newline
    state is untouched because only matched substrings are replaced.
    """
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    if "\r\n" in text:
        text = text.replace("\r\n", "\n")
    return text, _detect_newline(raw)


def _normalise_newlines(text: str) -> str:
    """Collapse CRLF and lone CR to LF so line endings can be re-applied once."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _write_edited(path, new_content: str, newline: str = "\n") -> None:
    """The one write path both modes share (called while holding the file lock).

    ``new_content`` is normalised to LF *first*: a model that quotes a block
    from a CRLF file passes ``\\r\\n`` inside ``new_string``, and converting
    that to the file's own ``\\r\\n`` afterwards would double every CR. With
    the normalisation up front the written bytes are exactly the intended text
    in the file's own line ending.
    """
    new_content = _normalise_newlines(new_content)
    if newline != "\n":
        new_content = new_content.replace("\n", newline)
    path.write_bytes(new_content.encode("utf-8"))


def _diagnostics(path, before: str, after: str, context) -> str:
    """Problems the edit introduced (see tools/diagnostics.py); never fails the edit."""
    try:
        return post_edit_report(path, before, _normalise_newlines(after), getattr(context, "workspace", None))
    except Exception:
        return ""


def _success_message(file_path: str, count: int, line_num: int) -> str:
    if count == 1:
        return f"Successfully replaced 1 occurrence in {file_path} @ L{line_num}"
    return f"Successfully replaced {count} occurrences in {file_path} (first @ L{line_num})"


@registry.register(
    name="edit",
    description=(
        "Perform exact string replacement in a file. old_string must match the file exactly, including "
        "whitespace and indentation, and must be unique unless replace_all is true. Read the file first "
        "and copy old_string from what you saw. To make several changes to ONE file in a single call, "
        "use replacements instead of calling edit once per change."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path or workspace-relative path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": "Exact text to find and replace (single replacement; use replacements for several).",
            },
            "new_string": {
                "type": "string",
                "description": "Text to replace it with.",
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "Replace every occurrence of old_string instead of requiring a unique match. "
                    "Use for renames across a file. Default: false."
                ),
                "default": False,
            },
            "replacements": {
                "type": "array",
                "description": (
                    "Several replacements applied to file_path in one call, in order: each item is "
                    "{old_string, new_string, replace_all?} and sees the result of the items before it. "
                    "All items are applied in memory and the file is written once at the end, so this is "
                    "one unit with one confirmation: if any item fails, nothing is written (all-or-nothing) "
                    "and the result lists every item's outcome. Use it whenever several changes touch the "
                    "same file. Provide either old_string/new_string or replacements, not both."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "old_string": {
                            "type": "string",
                            "description": "Exact text to find, unique unless replace_all is true.",
                        },
                        "new_string": {
                            "type": "string",
                            "description": "Text to replace it with.",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace every occurrence of this item's old_string. Default: false.",
                            "default": False,
                        },
                    },
                    "required": ["old_string", "new_string"],
                },
            },
        },
        "required": ["file_path"],
    },
    requires_permission=True,
)
def edit_file(
    file_path: str,
    old_string: str = None,
    new_string: str = None,
    replace_all: bool = False,
    replacements: list = None,
    context=None,
) -> str:
    path, error = resolve_tool_path(file_path, context)
    if error:
        return error
    assert path is not None

    single = isinstance(old_string, str) and isinstance(new_string, str)
    if (old_string is not None or new_string is not None) and replacements is not None:
        return (
            "Error: provide either old_string/new_string or replacements, not both. "
            "Use replacements for several changes to the same file in one call."
        )
    if not single and replacements is None:
        return (
            "Error: missing arguments: provide old_string and new_string, "
            "or replacements for several changes to the same file."
        )

    if not path.exists():
        return f"Error: file does not exist: {file_path}"

    batch: list | None = None
    if replacements is not None:
        if isinstance(replacements, dict):
            batch = [replacements]
        elif isinstance(replacements, list):
            batch = replacements
        else:
            return "Error: replacements must be an array of {old_string, new_string} objects."
        if not batch:
            return "Error: replacements must not be empty."
    else:
        assert isinstance(old_string, str) and isinstance(new_string, str)
        if not old_string:
            return "Error: old_string must not be empty. Use write to create a file or replace its whole content."
        if old_string == new_string:
            return "Error: old_string and new_string are identical; nothing to change."

    tracker = tracker_of(context)
    with file_lock(path):
        if tracker is not None:
            refusal = tracker.check(path, file_path)
            if refusal:
                return refusal
        try:
            content, newline = _read_preserving(path)
        except Exception as e:
            return f"Error reading file: {e}"

        items = batch if batch is not None else [
            {"old_string": old_string, "new_string": new_string, "replace_all": replace_all}
        ]
        new_content, outcomes = _apply_items(content, items)

        if batch is None:
            outcome = outcomes[0]
            if not outcome["ok"]:
                reason = outcome["reason"]
                count = content.count(old_string or "")
                if count == 0:
                    return f"Error: old_string not found in {file_path}"
                if count > 1 and not replace_all:
                    return (
                        f"Error: old_string found {count} times in {file_path}. Provide more context to "
                        "make it unique, or set replace_all=true to replace every occurrence."
                    )
                return f"Error: {reason}"
            _write_edited(path, new_content, newline)
            if tracker is not None:
                tracker.record(path, written=True)
            return _success_message(file_path, outcome["count"], outcome["line"]) + _diagnostics(
                path, content, new_content, context
            )

        applied = [o for o in outcomes if o["ok"]]
        total = len(outcomes)
        lines = [
            f"{index}. ok ({o['count']} replacement{'s' if o['count'] != 1 else ''})"
            if o["ok"]
            else f"{index}. {o['reason']}"
            for index, o in enumerate(outcomes, start=1)
        ]

        if not _batch_ok(outcomes):
            # All-or-nothing: the replacements batch is one unit, so if any item
            # fails nothing is written. Every item's outcome is still reported.
            failed = total - len(applied)
            return "\n".join(
                [f"Error: {failed} of {total} replacements failed in {file_path}; nothing was written."]
                + lines
            )

        _write_edited(path, new_content, newline)
        if tracker is not None:
            tracker.record(path, written=True)
        replaced = sum(o["count"] for o in applied)
        lines.append(f"Applied {len(applied)} of {total} replacements to {file_path}")
        lines.append(_success_message(file_path, replaced, applied[0]["line"]))
        return "\n".join(lines) + _diagnostics(path, content, new_content, context)
