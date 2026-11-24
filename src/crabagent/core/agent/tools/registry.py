from __future__ import annotations

import asyncio
import copy
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .diff_preview import build_diff_preview
from .schema_validation import coerce_args, validate_args


@dataclass
class ToolInfo:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable
    requires_permission: bool = False
    metadata: dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {"source": "builtin"}


logger = logging.getLogger(__name__)

# Built-ins whose success means a workspace file changed on disk. Every other
# tool (MCP, shell, office) stays out: FILE_UPDATED drives editor reloads.
_FILE_WRITE_TOOLS = frozenset({"write", "edit", "update_agents_md"})

# Permission keys that stand for a family of tools. The agent settings page
# offers "browser" as one switch; it covers every ``browser_*`` tool.
_PERMISSION_FAMILIES: dict[str, str] = {"browser": "browser_"}
# A tool without its own entry takes another tool's permission: run_tests runs shell
# commands, so an agent that may not use bash may not run tests either.
_PERMISSION_INHERITS: dict[str, str] = {"run_tests": "bash"}

# How many argument problems the validation error tells the model about; the
# rest are covered by a trailing ellipsis to keep the result string small.
_MAX_REPORTED_ARG_PROBLEMS = 5


def _confirm_accepts_diff_preview(callback: Callable) -> bool:
    """True when a confirm callback opts into the optional ``diff_preview`` kwarg.

    Existing callbacks (CLI, sub-agent forwarding) take ``(tool_name, args)``;
    passing a third argument to them would raise, so the preview is opt-in.
    """
    try:
        params = inspect.signature(callback).parameters
    except (TypeError, ValueError):
        return False
    if "diff_preview" in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


async def _request_confirm(callback: Callable, tool_name: str, arguments: dict, workspace) -> bool:
    """Ask the user to confirm a tool call, with a diff preview when supported.

    The preview is only built here — at the moment a confirmation really is
    requested — so tools that never confirm pay no file I/O.
    """
    if _confirm_accepts_diff_preview(callback):
        preview = build_diff_preview(tool_name, arguments, workspace)
        if preview is not None:
            return await callback(tool_name, arguments, diff_preview=preview)
    return await callback(tool_name, arguments)


def resolve_permission(permissions: dict[str, str] | None, tool_name: str) -> str | None:
    """Return the permission an agent profile sets for *tool_name*, if any.

    An entry for the exact tool name wins. Otherwise a family key applies,
    for example ``"browser": "deny"`` to ``browser_navigate``.
    """
    if not permissions:
        return None
    exact = permissions.get(tool_name)
    if exact is not None:
        return exact
    for key, prefix in _PERMISSION_FAMILIES.items():
        if tool_name.startswith(prefix) and key in permissions:
            return permissions[key]
    parent = _PERMISSION_INHERITS.get(tool_name)
    if parent:
        return resolve_permission(permissions, parent)
    return None


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolInfo] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        requires_permission: bool = False,
        metadata: dict[str, Any] | None = None,
    ):
        def decorator(func: Callable):
            self._tools[name] = ToolInfo(
                name=name,
                description=description,
                parameters=parameters,
                handler=func,
                requires_permission=requires_permission,
                metadata=metadata or {"source": "builtin"},
            )
            return func

        return decorator

    def clone(self) -> ToolRegistry:
        """Create an isolated registry while retaining all globally registered tools."""
        cloned = ToolRegistry()
        cloned._tools = self._tools.copy()
        return cloned

    def get(self, name: str) -> ToolInfo | None:
        return self._tools.get(name)

    def unregister(self, name: str) -> None:
        """Remove a tool from this registry instance without touching global registrations."""
        self._tools.pop(name, None)

    def list_tools(self) -> list[ToolInfo]:
        return list(self._tools.values())

    def tool_info_list(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "default_permission": "confirm" if t.requires_permission else "auto",
            }
            for t in self._tools.values()
        ]

    def tool_defs(self, exclude_names: set[str] | None = None) -> list[dict[str, Any]]:
        """Return tool definitions for LLM function calling.

        ``exclude_names`` supports per-request capability filtering without
        mutating the session's registry.
        """
        result = []
        excluded = exclude_names or set()
        for t in self._tools.values():
            if t.name in excluded:
                continue
            desc = t.description
            params = t.parameters

            # Display metadata belongs to the wrapper, not the tool/MCP handler.
            # A plain "summary" key is used because models reproduce it reliably;
            # tools that declare a real "summary" parameter keep their own schema.
            params = copy.deepcopy(params)
            properties = params.setdefault("properties", {})
            if "summary" not in properties:
                properties["summary"] = {
                    "type": "string",
                    "description": (
                        "Briefly explain to the user what this tool call is about to do, "
                        "in English unless the user writes in another language "
                        "(one short sentence, at most 40 characters). "
                        "Describe the action, not a claimed result; do not include secrets."
                    ),
                    "maxLength": 40,
                }
                params["required"] = list(dict.fromkeys([*params.get("required", []), "summary"]))
            result.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": desc,
                        "parameters": params,
                    },
                }
            )
        return result

    async def _take_molt_snapshot(self, name: str, arguments: dict[str, Any], context: Any) -> None:
        """Capture file content BEFORE the tool modifies it.

        Copies the pre-modification file into a staging directory so that
        the snapshot truly reflects the 'before' state.
        """
        if name not in ("write", "edit"):
            return
        raw = arguments.get("path") or arguments.get("file_path", "")
        if not raw:
            return
        try:
            from pathlib import Path as _Path

            resolved = _Path(raw).resolve()
            ws = context.workspace.resolve()
            rel = str(resolved.relative_to(ws))
        except (ValueError, Exception):
            return

        # Only snapshot each file once per round
        pending = context.metadata.setdefault("_pending_molt_files", set())
        if rel in pending:
            return  # Already captured before another tool in the same round

        # Copy the file content NOW (before the tool modifies it)
        import shutil

        staging = ws / ".crabagent" / "molts" / "_staging"
        src = ws / rel
        if src.exists() and src.is_file():
            try:
                dst = staging / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))
            except Exception:
                logger.debug("Failed to pre-copy %s for molt staging", rel, exc_info=True)
                return  # Can't stage — skip this file
        # else: file doesn't exist yet (new file) — nothing to snapshot

        pending.add(rel)

    async def _flush_molt_snapshot(self, context: Any) -> None:
        context.metadata.pop("_batch_molt", None)
        pending = context.metadata.pop("_pending_molt_files", None)
        if not pending:
            return
        from crabagent.core.database import async_session_factory
        from crabagent.core.molt.snapshot import _molt_id
        from crabagent.core.molt.store import create_molt, prune_molts

        files = sorted(pending)
        desc = f"Before: {', '.join(files[:3])}" + (f" +{len(files) - 3} more" if len(files) > 3 else "")
        ws = context.workspace.resolve()
        staging = ws / ".crabagent" / "molts" / "_staging"

        # Check if any staged files exist
        staged_files = []
        for fp in files:
            staged = staging / fp
            if staged.exists() and staged.is_file():
                staged_files.append(fp)

        if not staged_files:
            # All files were new (didn't exist before) — nothing to snapshot
            import shutil

            shutil.rmtree(str(staging), ignore_errors=True)
            return

        # Create the molt directory and move staged files into it
        import shutil

        mid = _molt_id()
        md = ws / ".crabagent" / "molts" / mid
        try:
            md.mkdir(parents=True, exist_ok=True)

            method = "copy"
            # If git repo, also save diff for context
            is_git = (ws / ".git").exists()
            if is_git:
                import asyncio

                diff_lines = []
                for fp in staged_files:
                    try:
                        proc = await asyncio.create_subprocess_exec(
                            "git",
                            "diff",
                            "--",
                            fp,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                            cwd=str(ws),
                        )
                        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                        if stdout and stdout.strip():
                            diff_lines.append(f"--- {fp}\n{stdout.decode('utf-8', errors='replace')}")
                    except Exception:
                        pass
                if diff_lines:
                    method = "git"
                    (md / "diff.txt").write_text("\n".join(diff_lines), encoding="utf-8")

            # Move staged files into molt directory
            for fp in staged_files:
                src = staging / fp
                dst = md / fp
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))

            # Clean up empty staging dir
            shutil.rmtree(str(staging), ignore_errors=True)
        except Exception:
            logger.warning("Failed to create molt snapshot directory", exc_info=True)
            shutil.rmtree(str(staging), ignore_errors=True)
            shutil.rmtree(str(md), ignore_errors=True)
            return

        snap = {
            "molt_id": mid,
            "description": desc,
            "method": method,
            "files": staged_files,
        }
        try:
            sess_id = context.metadata.get("session_id", "")
            branch_id = context.metadata.get("branch_id", "main")
            async with async_session_factory() as db:
                await create_molt(
                    db,
                    molt_id=snap["molt_id"],
                    session_id=sess_id,
                    branch_id=branch_id,
                    description=snap["description"],
                    method=snap["method"],
                    file_count=len(snap["files"]),
                    workspace=str(ws),
                )
                await prune_molts(workspace=ws)
        except Exception:
            logger.warning(
                "Failed to persist molt %s to DB, cleaning up orphaned files",
                snap.get("molt_id", ""),
                exc_info=True,
            )
            shutil.rmtree(str(md), ignore_errors=True)

    async def _emit_file_updated(self, name: str, arguments: dict[str, Any], context: Any, result: Any) -> None:
        """Announce a successful file write to the UI (open-buffer reloads).

        Only the file-mutating built-ins count, and only when the handler did not
        return an error string: the frontend reloads an open editor buffer per
        FILE_UPDATED, so a failed write must stay silent.
        """
        if context is None or name not in _FILE_WRITE_TOOLS:
            return
        if not isinstance(result, str) or result.lstrip().startswith("Error"):
            return
        if name == "update_agents_md":
            raw = "AGENTS.md"
        else:
            raw = str(arguments.get("file_path") or arguments.get("path") or "")
        if not raw:
            return
        try:
            ws = context.workspace.resolve()
            target = Path(raw).expanduser()
            if not target.is_absolute():
                target = ws / target
            target = target.resolve()
            rel = str(target.relative_to(ws))
            mtime_ms = int(target.stat().st_mtime * 1000)
        except (ValueError, OSError, AttributeError):
            return
        bus = getattr(context, "event_bus", None)
        if not bus:
            return
        from crabagent.core.event import AgentEvent, EventType

        try:
            await bus.emit(
                AgentEvent(
                    type=EventType.FILE_UPDATED,
                    data={
                        "path": rel,
                        "abs_path": str(target),
                        "mtime_ms": mtime_ms,
                        "tool": name,
                        "origin": "agent",
                    },
                )
            )
        except Exception:
            logger.debug("Failed to emit FILE_UPDATED for %s", name, exc_info=True)

    async def execute(self, name: str, arguments: dict[str, Any], context: Any = None) -> object:
        import time as _t

        tool = self._tools.get(name)
        if not tool:
            return f"Error: tool '{name}' is not available (may be restricted for current agent)"

        if "_truncated_error" in arguments:
            return arguments["_truncated_error"]

        # Keep the original arguments intact for events/history and UI rendering.
        # "summary" is display metadata injected by tool_defs, so it is dropped
        # unless the tool declares a real "summary" parameter of its own.
        # "_summary" is the legacy key emitted by older sessions; drop it too.
        declared = (tool.parameters or {}).get("properties", {}) or {}
        arguments = {
            key: value
            for key, value in arguments.items()
            if key != "_summary" and (key != "summary" or "summary" in declared)
        }
        _t0 = _t.monotonic()

        if context is not None:
            try:
                await self._take_molt_snapshot(name, arguments, context)
            except Exception:
                logger.warning("Molt snapshot failed for %s, continuing tool execution", name)

        if context is not None:
            permission = resolve_permission(context.tool_permissions, tool.name)
            if permission is None:
                permission = "confirm" if tool.requires_permission else "auto"
            if permission == "deny":
                return f"Tool '{tool.name}' is disabled (no permission for current agent)"
            if permission == "confirm":
                if tool.name not in context.approved_tools:
                    if context.confirm_callback:
                        approved = await _request_confirm(
                            context.confirm_callback,
                            tool.name,
                            arguments,
                            getattr(context, "workspace", None),
                        )
                        if not approved:
                            return f"Tool '{tool.name}' execution denied by user."
                    context.approved_tools.add(tool.name)

        try:
            sig = inspect.signature(tool.handler)
            kwargs = dict(arguments)
            # Drop stray keys the handler doesn't declare (models occasionally
            # invent parameter names such as task_id/description/workdir);
            # without this filter the whole call would fail with a TypeError.
            # Handlers with **kwargs (MCP / custom tools) accept everything.
            if not any(
                p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                for stray in [k for k in kwargs if k not in sig.parameters]:
                    logger.warning("Tool '%s': dropping unknown argument '%s'", name, stray)
                    kwargs.pop(stray)
            if not kwargs:
                missing = [
                    p_name
                    for p_name, p in sig.parameters.items()
                    if p_name != "context"
                    and p.default is inspect.Parameter.empty
                    and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
                ]
                if missing:
                    logger.warning(
                        "Tool '%s' called with empty arguments (missing: %s). "
                        "This usually means the LLM failed to generate tool call arguments.",
                        name,
                        missing,
                    )
                    return (
                        f"Error: tool '{name}' was called without any arguments. "
                        f"Required parameters: {', '.join(missing)}. "
                        f"Please retry the tool call with all required arguments."
                    )
            # Repair the common "nearly right" argument shapes the model sends
            # ("5" for integer, a JSON string for an object, ...) and prove the
            # result against the declared JSON Schema before the handler runs.
            # One insertion point covers sync, coroutine, MCP and custom tools.
            kwargs = coerce_args(tool.parameters, kwargs)
            problems = validate_args(tool.parameters, kwargs)
            if problems:
                shown = problems[:_MAX_REPORTED_ARG_PROBLEMS]
                detail = "; ".join(shown)
                if len(problems) > _MAX_REPORTED_ARG_PROBLEMS:
                    detail += "; …"
                logger.warning("Tool '%s': invalid arguments: %s", name, detail)
                return (
                    f"Error: invalid arguments for tool '{name}': {detail}. "
                    f"Fix the arguments and call the tool again."
                )
            if "context" in sig.parameters and context is not None:
                kwargs["context"] = context
            _t1 = _t.monotonic()
            if _t1 - _t0 > 0.1:
                logging.getLogger(__name__).info("tool %s: pre-exec took %.1fms", name, (_t1 - _t0) * 1000)
            if inspect.iscoroutinefunction(tool.handler):
                result = await tool.handler(**kwargs)
            else:
                result = await asyncio.to_thread(tool.handler, **kwargs)
            _elapsed = _t.monotonic() - _t0
            if _elapsed > 0.5:
                logging.getLogger(__name__).warning("tool %s SLOW %.1fs", name, _elapsed)
            # Flush pending molts immediately for direct tool calls (outside run_agent)
            if context is not None and not context.metadata.get("_batch_molt"):
                await self._flush_molt_snapshot(context)
            # Tell the UI an open editor buffer is stale (once per successful write)
            await self._emit_file_updated(name, arguments, context, result)
            # Preserve structured and multimodal outputs. The agent loop handles
            # protocol-safe serialization and moves image blocks to user messages.
            return result if isinstance(result, (str, list, dict)) else str(result)
        except Exception as e:
            return f"Error executing {name}: {e}"


registry = ToolRegistry()
