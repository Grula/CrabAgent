from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import secrets
import time as _time
from pathlib import Path

from fastapi import APIRouter, Depends, File as FastFile, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from crabagent.core.database import User, get_db
from crabagent.serve.deps import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/files", tags=["files"])

# ── Temporary HTTP servers for HTML preview ──────────────────────────
_html_servers: dict[str, Path] = {}
_html_server_lock = asyncio.Lock()


def _issue_preview_token(target: Path) -> str:
    token = secrets.token_urlsafe(24)
    _html_servers[token] = target
    return token

_IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp", ".avif"}
)


def _resolve_path(base: Path, raw: str) -> Path | None:
    resolved = (base / raw).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        return None
    return resolved


# Never served through /files, even though both live inside home: the app's own
# database/encryption key/uploads and the user's SSH material. Taken relative to
# Path.home() so the deny list and the home check always agree.
_ALWAYS_DENIED = (".crabagent", ".ssh")
_PATH_FORBIDDEN = "Path is not accessible"


def _is_within(path: Path, root: Path) -> bool:
    """True when ``path`` is ``root`` or below it (no string-prefix tricks)."""
    return path.is_relative_to(root)


def _denied_roots() -> list[Path]:
    home = Path.home().resolve()
    return [(home / name).resolve() for name in _ALWAYS_DENIED]


def _allowed_roots(own_workspace: Path | None = None) -> list[Path]:
    """Directories a /files request may touch: home and the server's workspace.

    ``own_workspace`` is the caller's chat workspace, which is user-settable via
    ``POST /sessions``; it only counts as a root when it does not widen the
    surface, i.e. it is already inside home or the server workspace. A chat
    pointed at ``/`` or ``/etc`` must not turn that directory into a root.
    """
    from crabagent.core.config import settings

    roots = [Path.home().resolve(), settings.workspace.resolve()]
    if own_workspace is not None and any(
        _is_within(own_workspace, root) for root in roots
    ):
        roots.append(own_workspace)
    return roots


def _is_denied_path(target: Path) -> bool:
    """True when ``target`` is (or sits inside) an always-denied directory."""
    return any(_is_within(target, denied) for denied in _denied_roots())


def _require_allowed_path(target: Path, own_workspace: Path | None = None) -> Path:
    """403 unless ``target`` sits under home, the server workspace or the chat's."""
    if _is_denied_path(target):
        raise HTTPException(status_code=403, detail=_PATH_FORBIDDEN)
    if any(_is_within(target, root) for root in _allowed_roots(own_workspace)):
        return target
    raise HTTPException(status_code=403, detail=_PATH_FORBIDDEN)


async def _request_base(
    db: AsyncSession, session_id: str | None, user: User
) -> tuple[Path, Path | None]:
    """Base directory for a ``/files`` request, plus the caller's own chat workspace.

    The second item is ``None`` whenever the base is the server workspace, so
    callers can tell a user-controlled base from an admin-configured one.
    """
    from crabagent.core.config import settings

    own = await _session_workspace(db, session_id, user)
    if own is not None:
        return own, own
    return settings.workspace.resolve(), None


def _conversation_workspace(conv: object | None) -> Path | None:
    """The conversation's existing workspace directory, or None when it has none."""
    raw = getattr(conv, "workspace", None)
    if not raw:
        return None
    try:
        path = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError):
        return None
    return path if path.is_dir() else None


async def _owned_conversation(
    db: AsyncSession, session_id: str | None, user: User
) -> object | None:
    """The caller's own conversation, or None for unknown/foreign/invalid ids.

    Ownership is independent of ``Conversation.workspace``: a new chat has
    ``workspace == ""`` and still owns its event stream and undo history, so
    "is this session mine" must never be asked as "does it have a directory".
    """
    if not session_id:
        return None
    try:
        from crabagent.serve.services import conversation as conv_svc

        conv = await conv_svc.get_conversation(db, session_id)
    except Exception:
        logger.debug("Session lookup failed for %s", session_id, exc_info=True)
        return None
    if conv is None or getattr(conv, "user_id", None) != getattr(user, "id", None):
        return None
    return conv


async def _session_workspace(
    db: AsyncSession, session_id: str | None, user: User
) -> Path | None:
    """The chat's own workspace, or None when the caller should fall back.

    A chat runs in ``Conversation.workspace`` (see ``serve/api/prompt.py``),
    which is not the server CWD the ``/files`` routes used historically. Only
    the owning user's conversation counts; anything else (unknown session,
    foreign session, missing/invalid directory) falls back to the server
    workspace so existing callers keep their behaviour.
    """
    return _conversation_workspace(await _owned_conversation(db, session_id, user))


async def _workspace_base(db: AsyncSession, session_id: str | None, user: User) -> Path:
    """Base directory for a ``/files`` request: the chat's workspace or the server CWD."""
    return (await _request_base(db, session_id, user))[0]


def _emit_file_updated(request: Request | None, session_id: str | None, data: dict) -> None:
    """Broadcast FILE_UPDATED to one session's SSE subscribers (API-handler style)."""
    if request is None or not session_id:
        return
    from crabagent.core.event import AgentEvent, EventType

    event = AgentEvent(type=EventType.FILE_UPDATED, data=data)
    seen: set[int] = set()
    for entry in list(getattr(request.app.state, "event_queues", {}).values()):
        if not isinstance(entry, tuple) or len(entry) < 2 or entry[0] != session_id:
            continue
        # Queue tuples come in two shapes: (sid, q, ts) and the production
        # (sid, critical_q, stream_q, ts). FILE_UPDATED is not critical, so it goes
        # on exactly one queue — the stream queue when there is one — following
        # prompt._sse_forward's rule; putting it on both delivers it twice.
        queue = entry[2] if len(entry) >= 4 else entry[1]
        if not hasattr(queue, "put_nowait") or id(queue) in seen:
            continue
        seen.add(id(queue))
        try:
            queue.put_nowait(event)
        except Exception:
            logger.debug("Failed to queue FILE_UPDATED", exc_info=True)


@router.get("/tree")
async def list_tree(
    path: str = Query("", description="Path relative to workspace, or absolute path when absolute=true"),
    depth: int = Query(1, ge=1, le=5),
    absolute: bool = Query(False, description="Treat path as absolute filesystem path"),
    include_root: bool = Query(
        False,
        description="Return {path, entries} instead of a bare list, naming the directory listed",
    ),
    session_id: str | None = Query(None, description="Chat session whose workspace to use"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if absolute:
        # No path means "the user's home": the only absolute root every /files
        # caller may list, and where the workspace picker starts.
        target = Path(path).resolve() if path else Path.home().resolve()
        _require_allowed_path(target)
        if not target.exists():
            raise HTTPException(status_code=404, detail="Path not found")
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Not a directory")
        entries = _build_tree(target, target, depth=depth, absolute=True)
        if include_root:
            return {"path": str(target), "entries": entries}
        return entries

    workspace, own_workspace = await _request_base(db, session_id, user)
    target = _resolve_path(workspace, path.lstrip("/"))
    if not target:
        raise HTTPException(status_code=400, detail="Path outside workspace")
    _require_allowed_path(target, own_workspace)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory")
    return _build_tree(target, workspace, depth=depth)


def _build_tree(dir_path: Path, base: Path, depth: int, absolute: bool = False) -> list[dict]:
    if depth <= 0:
        return []
    entries: list[dict] = []
    try:
        for child in sorted(dir_path.iterdir()):
            name = child.name
            if name.startswith(".") and name != ".crabagent":
                continue
            # Never offer a directory that /files itself refuses to open.
            if name in _ALWAYS_DENIED and _is_denied_path(child.resolve()):
                continue
            rel = str(child.resolve()) if absolute else str(child.relative_to(base))
            is_dir = child.is_dir()
            entry: dict = {"name": name, "path": rel, "type": "directory" if is_dir else "file"}
            if is_dir and depth > 1:
                entry["children"] = _build_tree(child, base, depth - 1, absolute)
            entries.append(entry)
    except PermissionError:
        pass
    return entries


# Directories to skip during search
_SEARCH_SKIP_DIRS = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".egg-info", ".next", ".nuxt", ".turbo", ".svelte-kit",
    "target", ".gradle", ".idea", ".vscode",
})

# ── Flat file index cache ────────────────────────────────────────────
# Build once, reuse for all searches until TTL expires.

_file_index_cache: dict[str, list[dict]] = {}  # key: root path → list of {name, path, type}
_file_index_ts: dict[str, float] = {}           # key: root path → build timestamp
_FILE_INDEX_TTL = 15.0  # seconds


def _build_file_index(root: Path, base: Path, absolute: bool) -> list[dict]:
    """Build a flat list of all file/dir entries using os.scandir (fast, no stat calls)."""
    entries: list[dict] = []
    stack = [(root, 20)]  # (dir, depth_remaining)

    while stack:
        dir_path, depth = stack.pop()
        if depth <= 0:
            continue
        try:
            with os.scandir(dir_path) as it:
                for entry in it:
                    name = entry.name
                    if name.startswith(".") and name != ".crabagent":
                        continue
                    is_dir = entry.is_dir(follow_symlinks=False)
                    if is_dir and name in _SEARCH_SKIP_DIRS:
                        continue
                    # Compute relative path once
                    full = os.path.join(str(dir_path), name)
                    # Same rule as _build_tree: never offer a denied directory.
                    if name in _ALWAYS_DENIED and _is_denied_path(Path(full).resolve()):
                        continue
                    if absolute:
                        rel = os.path.abspath(full)
                    else:
                        rel = os.path.relpath(full, str(base))
                    entries.append({"name": name, "path": rel, "type": "directory" if is_dir else "file"})
                    if is_dir:
                        stack.append((Path(full), depth - 1))
        except (PermissionError, OSError):
            pass

    return entries


@router.get("/search")
async def search_files(
    q: str = Query(..., min_length=1, description="Search query (matches file/dir names)"),
    path: str = Query("", description="Root path relative to workspace, or absolute path when absolute=true"),
    absolute: bool = Query(False, description="Treat path as absolute filesystem path"),
    limit: int = Query(200, ge=1, le=500, description="Max results"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Search file/dir names across the workspace (recursive, case-insensitive).

    Uses a cached flat file index (rebuilt every 15s) for instant filtering.
    """
    from crabagent.core.config import settings

    if absolute:
        root = Path(path).resolve() if path else Path("/")
        _require_allowed_path(root)
    else:
        workspace = settings.workspace.resolve()
        root = _resolve_path(workspace, path.lstrip("/"))
        if not root:
            raise HTTPException(status_code=400, detail="Path outside workspace")
        # Same guard as the absolute branch and /tree: a relative path must not
        # reach a denied directory (e.g. .crabagent under a $HOME workspace).
        _require_allowed_path(root)

    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail="Directory not found")

    cache_key = str(root)

    def _ensure_index() -> list[dict]:
        """Return cached index or rebuild if stale."""
        now = _time.monotonic()
        if (
            cache_key in _file_index_cache
            and (now - _file_index_ts.get(cache_key, 0)) < _FILE_INDEX_TTL
        ):
            return _file_index_cache[cache_key]
        # Build fresh index
        base = root if absolute else _resolve_path(settings.workspace.resolve(), "") or root
        idx = _build_file_index(root, base, absolute)
        _file_index_cache[cache_key] = idx
        _file_index_ts[cache_key] = now
        return idx

    # Run index build in thread pool (only hits filesystem on cache miss)
    index = await asyncio.to_thread(_ensure_index)

    # Filter — pure in-memory, instant.
    # Match against both file name and path for better discoverability.
    query_lower = q.lower()
    results = [e for e in index if query_lower in e["name"].lower() or query_lower in e["path"].lower()]
    return {"results": results[:limit], "total": len(results)}


_LANGUAGE_BY_EXTENSION = {
    ".py": "python",
    ".pyi": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".md": "markdown",
    ".mdx": "markdown",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cc": "cpp",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".lua": "lua",
    ".xml": "xml",
    ".dockerfile": "dockerfile",
}


def detect_language(path: str) -> str:
    """Best-effort language label for an editor file path."""
    name = Path(path).name.lower()
    if name in ("dockerfile", "makefile"):
        return name
    return _LANGUAGE_BY_EXTENSION.get(Path(path).suffix.lower(), "")


def decode_file_bytes(raw: bytes) -> tuple[str, str, bool]:
    """Decode file bytes losslessly. Returns (text, encoding, is_binary).

    A NUL byte means binary. Otherwise strict UTF-8 wins; the fallback is
    latin-1, which maps bytes 1:1 to code points, so writing the text back with
    the returned encoding reproduces the original bytes exactly. (surrogateescape
    would also be lossless but lone surrogates cannot be serialised to JSON.)
    """
    if b"\x00" in raw:
        return "", "", True
    try:
        return raw.decode("utf-8"), "utf-8", False
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("latin-1"), "latin-1", False
    except Exception:
        return "", "", True


MAX_TEXT_BYTES = 1024 * 1024


def read_text_file(
    target: Path,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict:
    """Read a text file for the editor without ever raising on binary content.

    Whole-file reads keep the 1 MB cap. With a line range the file is streamed,
    so a file far above the cap can still be paged without loading it all.
    """
    st = target.stat()
    info: dict = {
        "content": "",
        "truncated": False,
        "binary": False,
        "encoding": "utf-8",
        "size": st.st_size,
        "mtime_ms": int(st.st_mtime * 1000),
        "language": detect_language(target.name),
        "start_line": None,
        "end_line": None,
        "total_lines": None,
        "has_more": False,
    }

    if start_line is None and end_line is None:
        if st.st_size > MAX_TEXT_BYTES:
            info["truncated"] = True
            info["message"] = "File too large (>1MB); read it with start_line/end_line"
            return info
        text, encoding, is_binary = decode_file_bytes(target.read_bytes())
        if is_binary:
            info["binary"] = True
            info["message"] = "Binary file, no text content"
            return info
        info.update(
            content=text,
            encoding=encoding,
            start_line=1,
            total_lines=len(text.splitlines()),
        )
        info["end_line"] = info["total_lines"] or 1
        return info

    first = max(1, start_line or 1)
    last = end_line if end_line is not None else -1
    chunk: list[bytes] = []
    total = 0
    line_no = 0
    hit_byte_cap = False
    with target.open("rb") as fh:
        # Lines before `first` still have to be consumed: the only way to find
        # where line `first` starts is to read up to its newline. Each read is
        # capped like a page, so a huge single-line file cannot be materialised
        # here; a skipped line over the page cap therefore ends the read as
        # truncated (line `first` cannot be located without buffering it).
        while line_no < first - 1:
            raw_skip = fh.readline(MAX_TEXT_BYTES + 1)
            if not raw_skip:
                break
            if len(raw_skip) > MAX_TEXT_BYTES:
                info.update(
                    start_line=first,
                    end_line=first,
                    truncated=True,
                    message="An earlier line exceeds 1MB; read the file without start_line",
                )
                return info
            line_no += 1
        # From here every read is bounded by the remaining budget. Plain
        # iteration would buffer a whole line first, so a 100 MB single-line
        # file would be fully materialised before the byte cap is checked.
        while last < 0 or line_no < last:
            remaining = MAX_TEXT_BYTES - total
            raw_line = fh.readline(remaining + 1)
            if not raw_line:
                break
            line_no += 1
            if len(raw_line) > remaining:
                # The line alone is bigger than a page: return its bounded
                # prefix and stop, exactly like a byte-cap cut between lines.
                if remaining > 0:
                    chunk.append(raw_line[:remaining])
                    total += remaining
                hit_byte_cap = True
                break
            chunk.append(raw_line)
            total += len(raw_line)
        has_more = hit_byte_cap
        if not has_more and last >= 0 and line_no >= last:
            # An end_line page must still report whether a next line exists;
            # a single bounded byte is enough to tell.
            has_more = bool(fh.readline(1))
    text, encoding, is_binary = decode_file_bytes(b"".join(chunk))
    if is_binary:
        info["binary"] = True
        info["message"] = "Binary file, no text content"
        return info
    info.update(
        content=text,
        encoding=encoding,
        start_line=first,
        # An empty chunk (start_line past EOF) must not report an inverted range.
        end_line=max(first, first + len(chunk) - 1),
        truncated=hit_byte_cap,
        has_more=has_more,
    )
    return info


_FILE_CONTEXT_CONTENT_LIMIT = 64 * 1024

_EDITOR_TYPE_LABELS = {
    "document": "Office document",
    "code": "code file",
    "prototype": "HTML prototype",
    "markdown": "Markdown document",
}


def build_file_context_block(
    file_path: str,
    workspace: Path,
    *,
    workspace_type: str = "",
    dirty: bool = False,
    content: str = "",
) -> str:
    """Describe the file the user has open in the editor, for THIS run only.

    Called per prompt (never cached into ``conversations.system_prompt``): the
    open file changes constantly, so baking it into the cached prompt would
    either go stale or force a prompt-cache miss on every file switch.
    """
    if not file_path:
        return ""
    base = Path(workspace).resolve()
    raw = Path(file_path).expanduser()
    target = raw if raw.is_absolute() else base / file_path
    try:
        target = target.resolve()
    except (OSError, RuntimeError):
        return ""
    try:
        rel = str(target.relative_to(base))
    except ValueError:
        rel = file_path
    language = detect_language(str(target)) or "text"
    label = _EDITOR_TYPE_LABELS.get(workspace_type, "file")

    lines = [
        f"📎 [Open file] The user has `{rel}` open in the editor ({label}).",
        f"Path (workspace-relative): {rel}",
        f"Language: {language}",
    ]

    if dirty and content:
        buffer_content = content
        if len(buffer_content) > _FILE_CONTEXT_CONTENT_LIMIT:
            buffer_content = (
                buffer_content[:_FILE_CONTEXT_CONTENT_LIMIT]
                + "\n… [buffer truncated for context]"
            )
        fence = "````" if "```" in buffer_content else "```"
        lines.append(
            "The user has unsaved changes in the editor buffer. The disk copy of this "
            "file is STALE; the text below is the unsaved editor buffer and is what the "
            "user is looking at right now."
        )
        lines.append(f"Unsaved editor buffer:\n{fence}{language}\n{buffer_content}\n{fence}")
        return "\n".join(lines)

    if not target.is_file():
        lines.append("This file does not exist on disk yet.")
        return "\n".join(lines)
    info = read_text_file(target)
    if info["binary"]:
        lines.append("This file is binary; the editor shows no text for it.")
        return "\n".join(lines)
    lines.append("Read the file from disk if you need its contents.")
    return "\n".join(lines)


@router.get("/read")
async def read_file(
    path: str = Query(..., description="Relative path from workspace, or absolute path when absolute=true"),
    absolute: bool = Query(False, description="Treat path as absolute filesystem path"),
    start_line: int | None = Query(None, ge=1, description="First line to return (1-indexed, inclusive)"),
    end_line: int | None = Query(None, ge=1, description="Last line to return (1-indexed, inclusive)"),
    session_id: str | None = Query(None, description="Chat session whose workspace to use"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if absolute:
        target = Path(path).resolve()
        _require_allowed_path(target)
    else:
        workspace, own_workspace = await _request_base(db, session_id, user)
        t = _resolve_path(workspace, path.lstrip("/"))
        if not t:
            raise HTTPException(status_code=400, detail="Path outside workspace")
        target = t
        _require_allowed_path(target, own_workspace)

    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if not target.is_file():
        raise HTTPException(status_code=400, detail="Not a file")

    return {"path": path, **read_text_file(target, start_line, end_line)}


@router.post("/write")
async def write_file(
    req: WriteFileRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from crabagent.core.agent.tools.path_utils import file_lock

    # Only a session whose conversation belongs to the caller may drive the
    # file_updated broadcast and the molt row (item 5): a foreign id would pop a
    # false conflict in someone else's editor, so treat it as absent. Ownership
    # is not the same as "the chat already has a workspace directory" — a fresh
    # chat (workspace == "") still gets its event and its undo row.
    from crabagent.core.config import settings

    owned = await _owned_conversation(db, req.session_id, user)
    own_workspace = _conversation_workspace(owned)
    base = own_workspace if own_workspace is not None else settings.workspace.resolve()
    session_id = req.session_id if owned is not None else None

    if req.absolute:
        target = Path(req.path).resolve()
        if not target.is_relative_to(Path.home().resolve()):
            raise HTTPException(status_code=400, detail="Absolute path must be under home directory")
    else:
        t = _resolve_path(base, req.path.lstrip("/"))
        if not t:
            raise HTTPException(status_code=400, detail="Path outside workspace")
        target = t

    # The chat workspace is user-settable (POST /sessions), so a relative path
    # must not escape home/ownership through it (item 4).
    _require_allowed_path(target, own_workspace)

    try:
        rel = str(target.relative_to(base))
    except ValueError:
        rel = str(target)

    encoding = req.encoding if req.encoding in ("utf-8", "latin-1") else "utf-8"
    try:
        data = req.content.encode(encoding, errors="surrogateescape")
    except (UnicodeEncodeError, LookupError):
        raise HTTPException(status_code=400, detail=f"Cannot encode content as {encoding}")

    # Same per-file lock the write tool uses, so a UI save cannot interleave
    # with an agent write to the same file.
    lock = file_lock(target)
    await asyncio.to_thread(lock.acquire)
    try:
        current_mtime_ms = int(target.stat().st_mtime * 1000) if target.exists() else None
        if req.expected_mtime_ms is not None and req.expected_mtime_ms != current_mtime_ms:
            raise HTTPException(
                status_code=409,
                detail=(
                    "File changed on disk since it was opened "
                    f"(expected mtime {req.expected_mtime_ms} ms, found {current_mtime_ms} ms). "
                    "Reload the file before saving, or save without expected_mtime_ms to overwrite."
                ),
            )
        # Undo history: snapshot the pre-save content through the tool registry's
        # existing molt path, so /sessions/{id}/molts/{molt_id}/rollback restores it.
        await _snapshot_before_ui_write(base, target, session_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(target.write_bytes, data)
        st = target.stat()
        size = st.st_size
        mtime_ms = int(st.st_mtime * 1000)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to write file: {e}")
    finally:
        lock.release()

    _emit_file_updated(
        request,
        session_id,
        {
            "path": rel,
            "abs_path": str(target),
            "mtime_ms": mtime_ms,
            "tool": "editor",
            "origin": "ui",
        },
    )
    return {"status": "ok", "path": req.path, "size": size, "mtime_ms": mtime_ms}


async def _snapshot_before_ui_write(workspace: Path, target: Path, session_id: str | None) -> None:
    """Snapshot a file before an editor save, reusing the agent tools' molt code.

    An unowned session (a foreign or missing session_id) gets no snapshot: a
    molt row with an empty owner could never be listed or rolled back.
    """
    if not session_id:
        return

    from types import SimpleNamespace

    from crabagent.core.agent.tools.registry import registry as tool_registry

    ctx = SimpleNamespace(
        workspace=workspace,
        metadata={"session_id": session_id, "branch_id": "main"},
    )
    try:
        await tool_registry._take_molt_snapshot("write", {"path": str(target)}, ctx)
        await tool_registry._flush_molt_snapshot(ctx)
    except Exception:
        logger.warning("Molt snapshot failed for editor save of %s", target, exc_info=True)


class WriteFileRequest(BaseModel):
    path: str
    content: str
    absolute: bool = False
    session_id: str | None = None
    expected_mtime_ms: int | None = None
    encoding: str = "utf-8"


@router.get("/image")
async def get_image(
    path: str = Query(..., description="Relative path from workspace, or absolute path when absolute=true"),
    absolute: bool = Query(False, description="Treat path as absolute filesystem path"),
    token: str = Query(..., description="JWT token for img src auth"),
    db: AsyncSession = Depends(get_db),
):
    from crabagent.core.config import settings
    from crabagent.serve.services.auth import decode_access_token, get_user_by_id

    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        user_id = int(payload["sub"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = await get_user_by_id(db, user_id)
    if not user or not user.enabled:
        raise HTTPException(status_code=401, detail="User not found or disabled")

    if absolute:
        target = Path(path).resolve()
    else:
        workspace = settings.workspace.resolve()
        t = _resolve_path(workspace, path.lstrip("/"))
        if not t:
            raise HTTPException(status_code=400, detail="Path outside workspace")
        target = t

    _require_allowed_path(target)

    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    if target.suffix.lower() not in _IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Not an image file")

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(str(target), media_type=media_type)


@router.post("/serve-dir")
async def start_preview_session(
    path: str = Query(..., description="Absolute directory path to preview"),
    user: User = Depends(get_current_user),
):
    """Create a tokenized same-origin preview route for a directory."""
    target = Path(path).resolve()
    # The token is self-authenticating and the preview route has no auth of its
    # own, so the directory it pins must pass the same allow/deny rules as
    # every other /files route (audit item 2, via the preview door).
    _require_allowed_path(target)
    if not target.is_dir():
        raise HTTPException(status_code=400, detail="Not a directory or does not exist")

    async with _html_server_lock:
        preview_token = _issue_preview_token(target)

    return {"token": preview_token, "url": f"/api/files/preview/{preview_token}"}


@router.get("/preview/{preview_token}/{file_path:path}")
async def serve_preview_file(
    preview_token: str,
    file_path: str,
):
    """Serve files in a preview session. The preview_token is self-authenticating."""
    async with _html_server_lock:
        base_dir = _html_servers.get(preview_token)
    if not base_dir:
        raise HTTPException(status_code=404, detail="Preview session not found")

    target = _resolve_path(base_dir, file_path)
    if not target or not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    # Defence in depth: the pinned root was validated when the token was issued,
    # and the served file must stay under what every other route allows.
    _require_allowed_path(target)

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return FileResponse(str(target), media_type=media_type)


@router.post("/stop-server")
async def stop_preview_session(
    token: str = Query(..., description="Preview session token to stop"),
    user: User = Depends(get_current_user),
):
    async with _html_server_lock:
        removed = _html_servers.pop(token, None)
    return {"status": "stopped" if removed else "not_found", "token": token}


@router.get("/git-status")
async def git_status(
    workspace: str | None = Query(None, description="Absolute workspace path"),
    user: User = Depends(get_current_user),
):
    """Return git status and diff for the workspace if it's a git repo."""
    import asyncio

    from crabagent.core.config import settings as _settings

    ws = Path(workspace).resolve() if workspace else _settings.workspace.resolve()
    # A caller-supplied workspace must not read another user's repo.
    _require_allowed_path(ws)
    if not ws.is_dir() or not (ws / ".git").exists():
        return {"is_git": False, "changes": []}

    try:
        # git status --porcelain
        proc = await asyncio.create_subprocess_exec(
            "git", "status", "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ws),
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        status_lines = stdout.decode("utf-8", errors="replace").splitlines()

        changes = []
        for line in status_lines:
            if len(line) < 4:
                continue
            status_code = line[:2].strip()
            filepath = line[3:].strip()
            # Handle renames: "R  old -> new"
            if "->" in filepath:
                parts = filepath.split("->")
                filepath = parts[-1].strip().strip('"')
            else:
                filepath = filepath.strip('"')
            changes.append({"status": status_code, "file": filepath})

        # git diff --stat for changed lines summary
        diff_summary = ""
        if changes:
            proc2 = await asyncio.create_subprocess_exec(
                "git", "diff", "--stat",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ws),
            )
            stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=10)
            diff_summary = stdout2.decode("utf-8", errors="replace").strip()

        return {"is_git": True, "changes": changes, "diff_summary": diff_summary}

    except Exception as e:
        return {"is_git": True, "changes": [], "error": str(e)}


@router.get("/git-diff")
async def git_diff(
    path: str | None = Query(None, description="Specific file path, or all changes if omitted"),
    cached: bool = Query(False, description="Show staged changes"),
    workspace: str | None = Query(None, description="Absolute workspace path"),
    user: User = Depends(get_current_user),
):
    """Return git diff output for the workspace."""
    import asyncio

    from crabagent.core.config import settings as _settings

    ws = Path(workspace).resolve() if workspace else _settings.workspace.resolve()
    # A caller-supplied workspace must not read another user's repo.
    _require_allowed_path(ws)
    if not ws.is_dir() or not (ws / ".git").exists():
        return {"is_git": False, "diff": ""}

    try:
        MAX_DIFF = 50_000

        # Check if the file is untracked (??). git diff doesn't show untracked files,
        # so we need to use --no-index against /dev/null.
        if path:
            status_proc = await asyncio.create_subprocess_exec(
                "git", "status", "--porcelain", "--", path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(ws),
            )
            status_stdout, _ = await asyncio.wait_for(status_proc.communicate(), timeout=10)
            status_line = status_stdout.decode("utf-8", errors="replace").strip()
            if status_line.startswith("??"):
                # Untracked file: diff against /dev/null to show full content as added
                full_path = ws / path
                if full_path.is_file():
                    # Limit file size to avoid huge diffs
                    file_size = full_path.stat().st_size
                    if file_size > 100_000:
                        diff_text = f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n(file too large to display: {file_size} bytes)"
                        return {"is_git": True, "diff": diff_text, "truncated": True}
                    try:
                        file_content = full_path.read_text("utf-8", errors="replace")
                    except Exception:
                        file_content = "(binary file)"
                    diff_lines = [f"diff --git a/{path} b/{path}", "new file mode 100644",
                                  "--- /dev/null", f"+++ b/{path}"]
                    for i, line in enumerate(file_content.splitlines(), 1):
                        diff_lines.append(f"+{line}")
                    diff_text = "\n".join(diff_lines)
                    truncated = False
                    if len(diff_text) > MAX_DIFF:
                        diff_text = diff_text[:MAX_DIFF]
                        truncated = True
                    return {"is_git": True, "diff": diff_text, "truncated": truncated}
                else:
                    return {"is_git": True, "diff": "", "is_untracked_dir": True}

        args = ["git", "diff"]
        if cached:
            args.append("--cached")
        if path:
            args.extend(["--", path])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ws),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
        diff_text = stdout.decode("utf-8", errors="replace")

        # Truncate if too large
        truncated = False
        if len(diff_text) > MAX_DIFF:
            diff_text = diff_text[:MAX_DIFF]
            truncated = True

        return {"is_git": True, "diff": diff_text, "truncated": truncated}

    except Exception as e:
        return {"is_git": True, "diff": "", "error": str(e)}


# ── File management: delete / rename / create / download ─────────────


def _resolve_file_path(raw_path: str, absolute: bool = False) -> Path:
    """Resolve a file path safely, preventing path traversal."""
    if absolute:
        target = Path(raw_path).resolve()
        if any(_is_within(target, denied) for denied in _denied_roots()):
            raise HTTPException(status_code=403, detail=_PATH_FORBIDDEN)
        if not target.is_relative_to(Path.home().resolve()):
            raise HTTPException(status_code=400, detail="Absolute path must be under home directory")
        return target
    else:
        from crabagent.core.config import settings

        workspace = settings.workspace.resolve()
        t = _resolve_path(workspace, raw_path.lstrip("/"))
        if not t:
            raise HTTPException(status_code=400, detail="Path outside workspace")
        # The server workspace may be $HOME itself (the default is Path.cwd()),
        # so the relative branch needs the same deny list as the absolute one:
        # otherwise ".ssh/id_rsa" or ".crabagent" is reachable by download/delete.
        if _is_denied_path(t):
            raise HTTPException(status_code=403, detail=_PATH_FORBIDDEN)
        return t


class DeleteRequest(BaseModel):
    path: str
    absolute: bool = False


class RenameRequest(BaseModel):
    old_path: str
    new_path: str
    absolute: bool = False


class CreateRequest(BaseModel):
    path: str
    entry_type: str = "file"  # "file" or "directory"
    absolute: bool = False


class MoveRequest(BaseModel):
    paths: list[str]
    destination: str
    absolute: bool = False


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


@router.delete("/manage")
async def delete_entry(
    req: DeleteRequest,
    user: User = Depends(get_current_user),
):
    """Delete a file or directory."""
    import shutil

    target = _resolve_file_path(req.path, req.absolute)
    if not target.exists():
        raise HTTPException(status_code=404, detail="Path not found")
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"status": "ok", "path": req.path}


@router.post("/rename")
async def rename_entry(
    req: RenameRequest,
    user: User = Depends(get_current_user),
):
    """Rename or move a file/directory."""
    old = _resolve_file_path(req.old_path, req.absolute)
    new = _resolve_file_path(req.new_path, req.absolute)
    if not old.exists():
        raise HTTPException(status_code=404, detail="Source not found")
    if new.exists():
        raise HTTPException(status_code=409, detail="Target already exists")
    new.parent.mkdir(parents=True, exist_ok=True)
    old.rename(new)
    return {"status": "ok", "old_path": req.old_path, "new_path": req.new_path}


@router.post("/create")
async def create_entry(
    req: CreateRequest,
    user: User = Depends(get_current_user),
):
    """Create a new file or directory."""
    if req.entry_type not in {"file", "directory"}:
        raise HTTPException(status_code=400, detail="entry_type must be file or directory")
    target = _resolve_file_path(req.path, req.absolute)
    if target.exists():
        raise HTTPException(status_code=409, detail="Path already exists")
    target.parent.mkdir(parents=True, exist_ok=True)
    if req.entry_type == "directory":
        target.mkdir(parents=True)
    else:
        target.write_text("", encoding="utf-8")
    return {"status": "ok", "path": req.path, "type": req.entry_type}


@router.post("/move")
async def move_entries(
    req: MoveRequest,
    user: User = Depends(get_current_user),
):
    """Move one or more entries into an existing directory atomically."""
    if not req.paths:
        raise HTTPException(status_code=400, detail="At least one source path is required")
    if len(set(req.paths)) != len(req.paths):
        raise HTTPException(status_code=400, detail="Source paths must be unique")

    destination = _resolve_file_path(req.destination, req.absolute)
    if not destination.is_dir():
        raise HTTPException(status_code=400, detail="Destination must be an existing directory")

    sources = [_resolve_file_path(path, req.absolute) for path in req.paths]
    if any(not source.exists() for source in sources):
        raise HTTPException(status_code=404, detail="Source not found")
    if any(source == destination for source in sources):
        raise HTTPException(status_code=400, detail="Cannot move a directory into itself")
    if any(source.is_dir() and _is_relative_to(destination, source) for source in sources):
        raise HTTPException(status_code=400, detail="Cannot move a directory into itself")

    targets = [destination / source.name for source in sources]
    if len(set(targets)) != len(targets) or any(target.exists() for target in targets):
        raise HTTPException(status_code=409, detail="A target already exists")

    for source, target in zip(sources, targets, strict=True):
        source.rename(target)
    return {"status": "ok", "moved": len(sources)}


@router.get("/download")
async def download_file(
    path: str = Query(...),
    absolute: bool = Query(False),
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Download a file with token auth (for direct browser download)."""
    from crabagent.serve.services.auth import decode_access_token, get_user_by_id

    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = await get_user_by_id(db, int(user_id))
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    target = _resolve_file_path(path, absolute)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    media_type, _ = mimetypes.guess_type(str(target))
    return FileResponse(str(target), filename=target.name, media_type=media_type)


# ── General file upload ─────────────────────────────────────────────


def _get_uploads_dir(user_id: int) -> Path:
    """Get the user's uploads directory, creating it if needed."""
    base = Path.home() / ".crabagent" / "uploads" / str(user_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


# Max upload size: 20 MB
_MAX_UPLOAD_SIZE = 20 * 1024 * 1024


@router.post("/upload")
async def upload_file(
    file: UploadFile = FastFile(...),
    user: User = Depends(get_current_user),
):
    """Upload any file to the user's uploads directory.

    Files are stored in ~/.crabagent/uploads/{user_id}/ — NOT in the workspace.
    Returns the absolute path so the agent can read the file.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No file provided")

    content = await file.read()
    if len(content) > _MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="File too large (max 20MB)")

    uploads_dir = _get_uploads_dir(user.id)

    # Sanitize filename — prevent path traversal
    safe_name = Path(file.filename).name
    dest = uploads_dir / safe_name

    # Avoid overwriting: append timestamp suffix if file exists
    if dest.exists():
        stem = dest.stem
        suffix = dest.suffix
        import time as _time

        dest = uploads_dir / f"{stem}_{int(_time.time())}{suffix}"

    dest.write_bytes(content)

    return {
        "status": "ok",
        "file": file.filename,
        "size": len(content),
        "path": str(dest),
    }
