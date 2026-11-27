from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import os
import re
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.loop import _provider_error_message, run_agent
from crabagent.core.agent.system_prompt import build_system_prompt, is_current_prompt
from crabagent.core.agent.tools.registry import registry
from crabagent.core.config import settings
from crabagent.core.database import User, async_session_factory, get_db
from crabagent.core.event import AgentEvent, EventType
from crabagent.serve.deps import get_current_user, get_owned_conversation
from crabagent.serve.services import conversation as conv_svc
from crabagent.serve.services.message import get_messages, message_to_dict, save_message
from crabagent.serve.services.persistence import PersistenceListener

try:
    import crabagent.core.agent.tools.browser  # noqa: F401
except Exception:
    pass

try:
    import crabagent.core.agent.tools.collaboration_browser  # noqa: F401
    import crabagent.core.agent.tools.computer  # noqa: F401
except Exception:
    pass

try:
    import crabagent.core.agent.tools.scheduled_task  # noqa: F401
except Exception:
    pass

try:
    import crabagent.core.agent.tools.agent  # noqa: F401
except Exception:
    pass

try:
    import crabagent.core.agent.tools.custom_tool  # noqa: F401
except Exception:
    pass

try:
    import crabagent.core.agent.tools.office  # noqa: F401
except Exception:
    pass

logger = logging.getLogger(__name__)

router = APIRouter(tags=["prompt"])

_tasks: dict[str, asyncio.Task] = {}
_contexts: dict[str, AgentContext] = {}

_session_locks: dict[str, asyncio.Lock] = {}


def _should_finalize_goal(metadata: dict) -> bool:
    """Finalize the goal only for a turn that ran to completion.

    A stopped turn leaves the goal open: the user stopped it, so it must not be
    auto-completed or checkpointed behind their back.
    """
    from crabagent.core.goals.service import goal_finalization_required

    return not metadata.get("_run_cancelled") and goal_finalization_required(metadata)


def _should_continue_goal(snapshot: dict | None, metadata: dict) -> bool:
    """Only continue healthy goal turns; failures and stopped turns require the user."""
    return bool(
        snapshot
        and snapshot.get("status") == "active"
        and snapshot.get("auto_continue")
        and not metadata.get("_run_error")
        and not metadata.get("_agent_error")
        and not metadata.get("_run_cancelled")
    )


# The chat view recognizes the handover and goal-continuation wording below and
# folds those messages (frontend/src/lib/userMessageText.ts autoPrompt): keep them in step.
HANDOVER_PREFIX = "[Handover] "
# Handovers in a row without a user message in between (engineer -> architect ->
# engineer ...): after this many the next agent waits for the user.
MAX_CHAINED_HANDOVERS = 2
# One counter per chat that handed over, so the map is capped: a long-lived
# server must not keep an entry for every session it ever served.
_MAX_TRACKED_HANDOVER_CHATS = 256
_handover_chain: dict[str, int] = {}


def _handover_request(request: PromptRequest, switched: dict) -> PromptRequest:
    """The first turn of the agent a chat was just switched to, built from the handover summary."""
    source = switched.get("from") or "the previous agent"
    return PromptRequest(
        message=f"{HANDOVER_PREFIX}{source} handed this request over to you:\n\n{switched.get('summary', '')}",
        model=request.model,
        provider=request.provider,
        agent=switched["agent"],
        reasoning_effort=request.reasoning_effort,
        file_context=request.file_context,
        workspace_type=request.workspace_type,
        work_mode=request.work_mode,
    )


def _next_handover_depth(session_id: str, message: str) -> int | None:
    """How many handovers in a row this one would be, or None when the chain is at its limit.

    A message from the user (not a handover) starts a new chain and forgets the
    chat's counter, so the next chat to hand over starts from one again.
    """
    if (message or "").startswith(HANDOVER_PREFIX):
        depth = _handover_chain.get(session_id, 0)
    else:
        _handover_chain.pop(session_id, None)
        depth = 0
    if depth >= MAX_CHAINED_HANDOVERS:
        return None
    return depth + 1


def _remember_handover_depth(session_id: str, depth: int) -> None:
    """Record the chat's chain length, dropping the oldest chat past the cap."""
    _handover_chain[session_id] = depth
    while len(_handover_chain) > _MAX_TRACKED_HANDOVER_CHATS:
        _handover_chain.pop(next(iter(_handover_chain)), None)


def _goal_continuation_request(request: PromptRequest) -> PromptRequest:
    """Keep the selected execution settings for an automatic Goal turn."""
    return PromptRequest(
        message=(
            "Continue the active goal from the latest checkpoint. Work on the next "
            "concrete unfinished step, then record progress or verified completion."
        ),
        model=request.model,
        provider=request.provider,
        agent=request.agent,
        reasoning_effort=request.reasoning_effort,
        file_context=request.file_context,
        workspace_type=request.workspace_type,
        work_mode=request.work_mode,
    )


MERMAID_GENERATION_INSTRUCTIONS = """\
Mermaid reliability rules:
- Generate Mermaid only when it materially improves the response; otherwise use ordinary Markdown.
- For flowcharts, use the conservative syntax `flowchart LR` or `flowchart TD`.
  Give every node a simple ASCII identifier and quote every label:
  `A["Label"]` and `Q{"Question?"}`.
- Label every branch only with the pipe form: `Q -->|Yes| A`. Never use the ambiguous `-- label -->` form.
- Keep node and edge labels as plain text. Do not use HTML (including `<br/>`),
  Markdown, URLs, HTML entities, unescaped quotes, or Mermaid styling/directive
  syntax in labels. Prefer a semicolon or separate node instead of a line break.
- Avoid parser-sensitive punctuation in labels where possible, especially brackets,
  braces, parentheses, colons, and quotation marks. If exact wording needs it,
  simplify the wording rather than using unquoted labels.
- Before sending the answer, visually inspect every Mermaid block for balanced
  brackets/quotes and ensure all edges use a supported diagram-specific form.

Safe flowchart example:
```mermaid
flowchart LR
  A["Start"] --> B["Check version"]
  B --> C{"Update available?"}
  C -->|No| D["Stay current"]
  C -->|Yes| E["Show update notice"]
```
"""



def _save_image_temp(data_url: str) -> dict:
    import base64
    import hashlib
    import os
    import tempfile

    header, b64 = data_url.split(",", 1)
    mime = header.split(":")[1].split(";")[0]
    ext = mime.split("/")[1] if "/" in mime else "png"
    size_kb = len(b64) * 3 // 4 // 1024

    img_dir = os.path.join(tempfile.gettempdir(), "crabagent_images")
    os.makedirs(img_dir, exist_ok=True)
    h = hashlib.md5(b64.encode()).hexdigest()[:12]
    path = os.path.join(img_dir, f"{h}.{ext}")

    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(base64.b64decode(b64))

    return {"file_path": path, "mime": mime, "size_kb": size_kb}


_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_IMAGE_PATH_RE = re.compile(
    r"(?:^|[\s(])((?:/[^\s)]+)\.(?:png|jpe?g|gif|webp|bmp))(?:[\s)]|$)",
    re.IGNORECASE,
)


def _extract_local_images(message: str) -> list[dict]:
    blocks: list[dict] = []
    for match in _IMAGE_PATH_RE.finditer(message):
        path_str = match.group(1)
        if not os.path.isfile(path_str):
            continue
        ext = os.path.splitext(path_str)[1].lower()
        if ext not in _IMAGE_EXTS:
            continue
        mime = mimetypes.guess_type(path_str)[0] or "image/png"
        try:
            with open(path_str, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
        except OSError:
            continue
        size_kb = os.path.getsize(path_str) // 1024
        data_url = f"data:{mime};base64,{b64}"
        blocks.append(
            {
                "type": "image_url",
                "image_url": {"url": data_url},
                "file_path": path_str,
                "mime": mime,
                "size_kb": size_kb,
            }
        )
    return blocks


class PromptRequest(BaseModel):
    message: str
    model: str | None = None
    provider: str | None = None
    images: list[str] | None = None
    agent: str | None = None
    reasoning_effort: str | None = None
    file_context: str = ""
    # Editor state for the open file. Injected per run (never into the cached
    # system prompt) — see build_file_context_block in serve/api/files.py.
    file_context_dirty: bool = False
    file_context_content: str = ""
    workspace_type: str = ""
    work_mode: bool = False


def profile_tool_names(profile: dict) -> list[str]:
    """The tools an agent profile actually gets, after its deny rules.

    Capability hints in the saved agent-switch message must describe these, not
    the global pool: an agent told it can drive the browser, or not told that it
    can't edit files, works from a false picture of itself on every later turn.
    """
    from crabagent.core.agent.agent_switch import filter_tool_registry

    tp = profile.get("tool_permissions") or {}
    narrowed = filter_tool_registry(registry, tool_permissions=tp if isinstance(tp, dict) else {}, allow_delegation=True)
    return list(narrowed._tools)


async def apply_agent_profile(context, effective_agent: str) -> None:
    """Narrow a chat context's tools and skills to the active agent's profile.

    The unnarrowed pool is kept in ``context.base_tool_registry`` so sub-agents
    can still be given tools and skills this agent does not see.
    """
    from crabagent.core.agent.agent_switch import filter_tool_registry
    from crabagent.core.agent.agents import get_agent
    from crabagent.core.agent.skill.loader import scope_skill_tool

    if effective_agent == "default":
        profile = await get_agent("__default__")
        if not profile:
            return
        tp = profile.get("tool_permissions", {})
        context.tool_permissions = tp if isinstance(tp, dict) else {}
        if profile.get("skills") is not None:
            context.base_tool_registry = context.base_tool_registry or context.tool_registry
            context.tool_registry = context.tool_registry.clone()
            scope_skill_tool(context.tool_registry, profile["skills"])
        return

    agent_def = await get_agent(effective_agent)
    if not agent_def:
        return
    tp = agent_def.get("tool_permissions", {})
    context.tool_permissions = tp if isinstance(tp, dict) else {}
    # Snapshot the unpruned pool first: the main agent may only expose a
    # narrowed tool list to the model, but sub-agents must still be able
    # to inherit tools this profile denies.
    context.base_tool_registry = context.tool_registry
    context.tool_registry = filter_tool_registry(
        context.tool_registry,
        tool_permissions=context.tool_permissions,
        allow_delegation=True,
    )
    scope_skill_tool(context.tool_registry, agent_def.get("skills"))
    if agent_def.get("model"):
        context.model = agent_def["model"]
    if agent_def.get("compaction_max_tokens"):
        context.metadata["compaction_max_tokens"] = agent_def["compaction_max_tokens"]


async def _report_run_failure(context: AgentContext, exc: BaseException) -> None:
    """Emit the AGENT_ERROR a failed run owes the UI.

    The run task's outer ``except`` only sets ``_run_error`` and logs, so the
    user saw AGENT_END (a stopped run) with no reason. The message comes from
    the same helper the loop uses, so a 401 reads the same however deep it was
    raised.
    """
    try:
        message = _provider_error_message(
            exc,
            context.metadata.get("resolved_provider") or context.provider_name,
            context.metadata.get("resolved_model") or context.model,
        )
        await context.event_bus.emit(
            AgentEvent(
                type=EventType.AGENT_ERROR,
                data={"error": message, "stage": "agent_task"},
            )
        )
    except Exception:
        logger.warning("Failed to emit AGENT_ERROR", exc_info=True)


@router.post("/sessions/{session_id}/prompt", status_code=status.HTTP_202_ACCEPTED)
async def prompt_async(
    session_id: str,
    req: PromptRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conv = await get_owned_conversation(db, session_id, user)

    if session_id in _tasks and not _tasks[session_id].done():
        context = _contexts.get(session_id)
        if not context or not context.metadata.get("_accept_followups") or context.budget_exhausted:
            raise HTTPException(status_code=409, detail="Session is finishing; retry shortly")
        # The open editor file is per-prompt context, so a follow-up sent while
        # the agent is still running must carry it too — same helper as the
        # new-run path, never duplicated logic.
        queued_text = req.message
        if req.file_context:
            try:
                from crabagent.serve.api.files import build_file_context_block

                _file_block = build_file_context_block(
                    req.file_context,
                    context.workspace,
                    workspace_type=req.workspace_type,
                    dirty=req.file_context_dirty,
                    content=req.file_context_content,
                )
                if _file_block:
                    queued_text = f"{_file_block}\n\n{queued_text}"
            except Exception:
                logger.debug("Failed to build active file context for follow-up", exc_info=True)
        content: str | list[dict] = queued_text
        local_images = _extract_local_images(req.message)
        if req.images or local_images:
            image_blocks = [
                {"type": "image_url", "image_url": {"url": image}, **_save_image_temp(image)}
                for image in (req.images or [])
            ] + local_images
            content = [{"type": "text", "text": queued_text}] + image_blocks
            context.metadata["attached_image_paths"] = [block["file_path"] for block in image_blocks]
        context.metadata.setdefault("_pending_user_messages", []).append(content)
        return {"status": "queued", "session_id": session_id}

    active_branch = conv.active_branch or "main"
    history_msgs = await get_messages(db, conv.id, branch_id=active_branch)
    user_msg_seq = max((m.sequence for m in history_msgs), default=0) + 1

    # --- Determine effective agent and whether a switch message is needed ---
    effective_agent = req.agent or getattr(conv, "agent", None) or "default"
    if effective_agent == "default":
        # A configured "default agent for new chats" replaces the plain assistant.
        from crabagent.core.agent.agents import resolve_default_agent_name

        effective_agent = await resolve_default_agent_name()

    last_agent = "default"
    for m in reversed(history_msgs):
        if m.role == "stats":
            continue
        agent = getattr(m, "agent", None) or "default"
        if agent != "default":
            last_agent = agent
            break

    agent_changed = last_agent != effective_agent

    # If agent changed, persist agent_switch BEFORE the user message
    if agent_changed and effective_agent != "default":
        from crabagent.core.agent.agents import build_agent_switch_msg, capability_hint_lines, get_agent

        agent_profile = await get_agent(effective_agent)
        if agent_profile:
            switch_msg = build_agent_switch_msg(
                agent_profile,
                extra_lines=capability_hint_lines(profile_tool_names(agent_profile)),
            )
            switch_seq = user_msg_seq
            await save_message(
                db,
                conversation_id=conv.id,
                sequence=switch_seq,
                role="agent_switch",
                content=switch_msg["content"],
                agent=effective_agent,
                branch_id=active_branch,
            )
            user_msg_seq += 1  # make room for the user message
    elif agent_changed and effective_agent == "default":
        # Switching from a specific agent back to default
        from crabagent.core.agent.agents import build_agent_switch_msg, capability_hint_lines, get_agent

        default_profile = await get_agent("__default__")
        if default_profile:
            switch_msg = build_agent_switch_msg(
                default_profile,
                extra_lines=capability_hint_lines(profile_tool_names(default_profile)),
            )
            switch_seq = user_msg_seq
            await save_message(
                db,
                conversation_id=conv.id,
                sequence=switch_seq,
                role="agent_switch",
                content=switch_msg["content"],
                agent="default",
                branch_id=active_branch,
            )
            user_msg_seq += 1

    if agent_changed:
        try:
            await conv_svc.update_conversation(db, session_id, agent=effective_agent)
        except Exception:
            pass

    # --- Workspace context switch (same pattern as agent_switch) ---
    _work_changed = (
        req.work_mode
        and req.file_context
        and (req.file_context != (conv.current_file or "") or req.workspace_type != (conv.workspace_type or ""))
    )
    if _work_changed:
        file_name = req.file_context.split("/")[-1]
        _type_labels = {
            "document": "Office document",
            "code": "code file",
            "prototype": "prototype/HTML",
            "markdown": "Markdown document",
        }
        _type_label = _type_labels.get(req.workspace_type, "file")
        _hints = {
            "document": (
                "The user is editing this Office document. Use the office_edit tool to modify it; "
                "no file path is needed.\n"
                "⚠️ After every document change, start the reply with \"📝 Change summary:\" and list what you changed."
            ),
            "code": "The user is editing this code file.",
            "prototype": "The user is editing this HTML prototype; a live preview is shown on the right.",
            "markdown": "The user is editing this Markdown document.",
        }
        _hint = _hints.get(req.workspace_type, "The user is editing this file.")
        ws_content = f"📎 [Workspace switch] {_type_label}: {file_name}\nPath: {req.file_context}\n{_hint}"
        ws_seq = user_msg_seq
        await save_message(
            db,
            conversation_id=conv.id,
            sequence=ws_seq,
            role="workspace",
            content=ws_content,
            branch_id=active_branch,
        )
        user_msg_seq += 1
        # Persist current file for next-change detection
        try:
            await conv_svc.update_conversation(
                db,
                session_id,
                current_file=req.file_context,
                workspace_type=req.workspace_type,
            )
        except Exception:
            pass

    # --- Build content & save user message (with updated seq if switch was inserted) ---
    local_images = _extract_local_images(req.message)

    if req.images:
        import json as _json

        content_blocks = [{"type": "text", "text": req.message}]
        for img in req.images:
            meta = _save_image_temp(img)
            content_blocks.append(
                {
                    "type": "image_url",
                    "image_url": {"url": img},
                    "file_path": meta["file_path"],
                    "mime": meta["mime"],
                    "size_kb": meta["size_kb"],
                }
            )
        content_blocks.extend(local_images)
        db_content = _json.dumps(content_blocks)
    elif local_images:
        import json as _json

        content_blocks = [{"type": "text", "text": req.message}] + local_images
        db_content = _json.dumps(content_blocks)
    else:
        db_content = req.message

    saved_msg = await save_message(
        db,
        conversation_id=conv.id,
        sequence=user_msg_seq,
        role="user",
        content=db_content,
        branch_id=active_branch,
    )
    _pending_fts_id = saved_msg.id

    # Index outside the request path; search catch-up will retry it after a restart.
    try:
        from crabagent.core.fts import index_message

        asyncio.create_task(index_message(_pending_fts_id, db_content))
    except Exception:
        pass

    workspace = Path(conv.workspace) if conv.workspace else Path.cwd()
    workspace = workspace.resolve()

    # Reuse cached system prompt if available — preserves LLM prefix cache
    _use_cache = False
    if conv.system_prompt:
        _use_cache = True
        # Detect raw/unresolved translation keys — indicates a broken cache
        # (e.g., server process had stale i18n data when the prompt was generated)
        if any(
            marker in conv.system_prompt
            for marker in (
                "team_prompt.",
                "agent_system.",
                "memory_prompt.",
                "shared_context.",
            )
        ):
            _use_cache = False
            logger.info(
                "[CACHE] Session %s: MISS (raw translation keys detected in cached prompt)",
                session_id,
            )
        if _use_cache and not is_current_prompt(conv.system_prompt):
            # Cached under an older base-prompt revision — rebuild it.
            _use_cache = False
            logger.info(
                "[CACHE] Session %s: MISS (cached prompt is from an older revision)",
                session_id,
            )
        if _use_cache:
            base_prompt = conv.system_prompt
            logger.info(
                "[CACHE] Session %s: HIT (prompt_len=%d)",
                session_id,
                len(conv.system_prompt),
            )

    if not _use_cache:
        logger.info("[CACHE] Session %s: MISS (rebuilding system prompt)", session_id)
        base_prompt = await build_system_prompt(
            workspace,
            user_id=user.id,
            interface="web",
            query=(req.message or "")[:500],
        )

        # Persist for subsequent messages in the same session
        try:
            from crabagent.serve.services.conversation import update_conversation

            async with async_session_factory() as save_db:
                await update_conversation(save_db, session_id, system_prompt=base_prompt)
            logger.info("[CACHE] Session %s: SAVED (prompt_len=%d)", session_id, len(base_prompt))
        except Exception as e:
            logger.warning("[CACHE] Session %s: SAVE FAILED: %s", session_id, e)

    context = AgentContext(
        workspace=workspace,
        tool_registry=registry.clone(),
        max_iterations=settings.max_iterations,
        model=req.model or conv.model or None,
        # Preserve the conversation provider if a Goal request omits it.
        provider_name=req.provider or conv.provider or None,
        system_prompt=base_prompt,
    )
    # Files read in an earlier turn of this chat can be edited in this one.
    from crabagent.core.agent.tools.file_state import session_tracker

    # switch_agent's new agent starts right away here (see the end of _run).
    context.metadata["_auto_handover"] = True

    context.file_state = session_tracker(session_id)
    try:
        from crabagent.core.goals.service import get_current_goal, goal_prompt
        from crabagent.core.goals.tools import register_goal_tools

        active_goal = await get_current_goal(db, session_id)
        if active_goal:
            context.metadata["goal_id"] = active_goal.id
            context.system_prompt += goal_prompt(active_goal)
            register_goal_tools(context, session_id, user.id)
            if active_goal.execution_model:
                context.model = active_goal.execution_model
            if active_goal.execution_provider:
                context.provider_name = active_goal.execution_provider
            if active_goal.reasoning_effort:
                context.metadata["reasoning_effort"] = active_goal.reasoning_effort
    except Exception:
        logger.exception("Failed to attach active goal for session %s", session_id)
    context.system_prompt += (
        """

## Rich response visualizations
When a visualization would improve the answer, use a fenced Markdown code block only.
- Use ```mermaid for flowcharts, sequence diagrams, state diagrams, ER diagrams,
  and architecture relationships. Follow the Mermaid reliability rules below exactly.
- Use ```crab-chart for data charts with JSON: version must be 1; type is bar, line,
  area, pie, or scatter; include x.field, data, and series. Example:
  {"version":1,"type":"bar","title":"Monthly revenue",
  "x":{"field":"month","label":"Month"},
  "series":[{"field":"revenue","name":"Revenue"}],
  "data":[{"month":"Jan","revenue":120}]}.
  All values must be JSON primitives.
- Use ```crab-kpi for a single metric with JSON: version must be 1; title and value
  are required; trend may be up, down, or neutral.
Use the object series format for new charts, for example:
"series":[{"field":"revenue","name":"Revenue"}] and "x":{"field":"month"}.
Do not use the legacy string-array series format.
Never put HTML, SVG, JavaScript, event handlers, URLs, or executable code in
visualization blocks. Do not fabricate data; explain when data is insufficient.
Use ordinary Markdown when a visualization is not helpful.

"""
        + MERMAID_GENERATION_INSTRUCTIONS
        + """
"""
    )
    if req.file_context:
        context.metadata["current_doc"] = req.file_context
        if req.file_context_dirty:
            # Sub-agents are told not to edit it (agents.unsaved_editor_note).
            context.metadata["unsaved_editor_file"] = req.file_context
    if req.workspace_type:
        context.metadata["workspace_type"] = req.workspace_type
    if req.work_mode:
        context.metadata["work_mode"] = req.work_mode

    if req.images or local_images:
        attached_images = [block.get("file_path", "") for block in content_blocks if block.get("type") == "image_url"]
        context.metadata["attached_image_paths"] = [path for path in attached_images if path]
        context.system_prompt += (
            "\n\nWhen the user asks to generate or modify an image based on an attached image, "
            "use the image_edit tool. It uses the most recently attached image automatically; "
            "do not use image_generate for reference-image requests."
        )

        from crabagent.core.agent.token_limits import is_vision_model

        resolved = req.model or conv.model or ""
        if not is_vision_model(resolved):
            context.system_prompt += (
                "\n\nThe user has attached images. This model cannot view images directly. "
                "The images are saved as local files. "
                "Use available MCP tools or file analysis tools to process the images, then describe what you find."
            )

    from crabagent.core.agent.skill.loader import discover_skills, register_skill_tool

    skill_dirs = settings.skill_discovery_dirs(workspace)
    skills = discover_skills(skill_dirs)
    if skills:
        register_skill_tool(context.tool_registry, skills)

    from crabagent.core.molt.tools import register_molt_tools

    register_molt_tools(context.tool_registry)

    from crabagent.core.todo.tools import register_todo_tools

    register_todo_tools(context.tool_registry)

    from crabagent.core.task.tools import register_task_tools

    register_task_tools(context.tool_registry)

    from crabagent.core.meeting.tools import register_meeting_tools

    register_meeting_tools(context.tool_registry)

    from crabagent.core.mail.tools import register_mail_tools

    register_mail_tools(context.tool_registry)

    from crabagent.core.calendar.tools import register_calendar_tools

    register_calendar_tools(context.tool_registry)

    from crabagent.core.tool_loader import discover_and_register_tools

    discover_and_register_tools(context.tool_registry, workspace)

    from crabagent.core.mcp.tools import register_mcp_tools

    mcp_manager = request.app.state.mcp_manager
    register_mcp_tools(context.tool_registry, mcp_manager)
    context.metadata["mcp_status"] = mcp_manager.get_status()

    context.metadata["session_id"] = session_id
    context.metadata["branch_id"] = active_branch
    context.metadata["user_id"] = user.id
    from crabagent.core.conversations.history import history_access_enabled

    context.metadata["conversation_history_tool_enabled"] = await history_access_enabled(user.id)
    if not context.metadata["conversation_history_tool_enabled"]:
        context.tool_registry.unregister("conversation_search")
    context.metadata["reasoning_effort"] = req.reasoning_effort or settings.reasoning_effort

    context.current_agent = effective_agent
    context.metadata["_current_agent"] = effective_agent

    from crabagent.core.agent.agents import build_agent_switch_msg, get_agent
    from crabagent.core.agent.agents import inject_agent_lessons as _inject_lessons

    await apply_agent_profile(context, effective_agent)

    # Add history messages first (any persisted agent_switch messages are included
    # in their correct positions via message_to_dict which converts them to user role)
    for msg_record in history_msgs:
        if msg_record.role == "stats":
            continue
        context.messages.append(message_to_dict(msg_record))

    # Restore accumulated token count for compression threshold check
    if conv.tokens:
        context.total_tokens = conv.tokens

    # Add agent_switch message AFTER history, RIGHT BEFORE the user message
    # so the LLM sees the role switch as the most recent context before responding
    if agent_changed:
        from crabagent.core.agent.agents import capability_hint_lines

        # Capability hints must reflect the FILTERED registry (after deny rules).
        _cap_lines = capability_hint_lines(context.tool_registry._tools.keys())
        if effective_agent == "default":
            default_profile = await get_agent("__default__")
            if default_profile:
                context.messages.append(
                    build_agent_switch_msg(default_profile, extra_lines=_cap_lines)
                )
        else:
            agent_def = await get_agent(effective_agent)
            if agent_def:
                context.messages.append(
                    build_agent_switch_msg(agent_def, extra_lines=_cap_lines)
                )

        # Inject per-agent lessons (not cached in system prompt)
        try:
            from crabagent.core.agent.agents import lesson_workspace

            lessons_block = await _inject_lessons(
                "",
                user_id=user.id,
                agent_name=effective_agent,
                task_hint=(req.message or "")[:120],
                workspace_path=lesson_workspace(context),
            )
            if lessons_block.strip():
                context.messages.append(
                    {"role": "experience", "content": lessons_block.strip(), "agent": effective_agent}
                )
        except Exception:
            pass

    # ── Active file context (this run only) ──
    # Deliberately in the messages, not the cached system prompt: the open file
    # changes constantly, so caching it would go stale or cost a prompt-cache
    # miss on every file switch. Same pattern as the workspace-switch message.
    if req.file_context:
        try:
            from crabagent.serve.api.files import build_file_context_block

            _file_block = build_file_context_block(
                req.file_context,
                workspace,
                workspace_type=req.workspace_type,
                dirty=req.file_context_dirty,
                content=req.file_context_content,
            )
            if _file_block:
                context.messages.append(
                    {"role": "user", "content": _file_block, "agent": effective_agent}
                )
        except Exception:
            logger.debug("Failed to build active file context", exc_info=True)

    persistence = PersistenceListener(conversation_id=conv.id, branch_id=active_branch)
    persistence.sequence = user_msg_seq
    context.event_bus.subscribe(persistence.on_event)
    # Expose to compress.py for inline persistence during compression
    context.metadata["_persistence"] = persistence

    from crabagent.core.agent.run_recorder import RunRecorder

    # context.model already includes the agent profile's own model, which wins over the UI pick.
    resolved_model = context.model or req.model or conv.model or ""
    run_recorder = RunRecorder(user_id=user.id, session_id=session_id, model=resolved_model)
    context.event_bus.subscribe(run_recorder.on_event)

    # ── Trusted work system: drive linked task lifecycle from this session ──
    # Tasks created in this conversation (source_session) are automatically
    # advanced by the conversation's execution: open tasks start with the
    # agent run and are judged (done/partial/failed) when it ends, so
    # task_updated events (with result cards) reach the chat and panels.
    from crabagent.core.task.service import TaskLifecycleLinker

    task_linker = TaskLifecycleLinker(user_id=user.id, session_id=session_id)

    async def _task_lifecycle_handler(event: AgentEvent):
        await task_linker.handle_event(
            event,
            link_run=run_recorder.link_task_run,
            unlink_run=run_recorder.unlink_task_run,
        )

    context.event_bus.subscribe(_task_lifecycle_handler)

    # ── Incremental token-usage flush ──
    # Write each finished iteration's usage row to DB as soon as its
    # ITERATION_END fires, so the chat stats bar can show mid-turn usage.
    # The post-run batch write in _run() stays as a safety net and only
    # flushes records that were not yet persisted (usage_flushed cursor).
    usage_flushed = 0

    async def _usage_flush_handler(event: AgentEvent):
        nonlocal usage_flushed
        if event.type not in (EventType.ITERATION_END, EventType.SUB_AGENT_END):
            return
        records = context.usage_records[usage_flushed:]
        if not records:
            return
        try:
            from crabagent.core.database import token_usage_batch_create

            model = context.metadata.get("resolved_model", context.model or "")
            provider = context.metadata.get("resolved_provider", "")
            await token_usage_batch_create(
                [
                    {
                        "user_id": user.id,
                        "session_id": session_id,
                        "agent_name": context.current_agent,
                        "model": model,
                        "provider": provider,
                        "branch_id": active_branch,
                        **r,
                    }
                    for r in records
                ]
            )
            usage_flushed += len(records)
        except Exception:
            # Cursor not advanced — the post-run batch write will retry these.
            logger.debug("Incremental token_usage flush failed", exc_info=True)

    context.event_bus.subscribe(_usage_flush_handler)


    queues = request.app.state.event_queues

    _fwd_count = 0
    _fwd_last_log = time.time()
    _throttle_until: dict[str, float] = {}
    _THROTTLE_INTERVAL = 0.1
    _THROTTLED_EVENTS: set[str] = set()
    _SKIP_GLOBAL_EVENTS: set[str] = set()
    _last_stale_cleanup = time.time()

    _CRITICAL_EVENT_TYPES = {EventType.TOOL_CONFIRM_REQUEST, EventType.USER_INPUT_REQUEST}

    async def _sse_forward(event: AgentEvent):
        nonlocal _fwd_count, _fwd_last_log, _last_stale_cleanup
        _fwd_count += 1
        now = time.time()

        # Keep the monitor summary authoritative across SSE reconnects and UI refreshes.
        active_info = request.app.state.active_agents.get(session_id)
        if active_info:
            if event.type in (EventType.TOOL_CONFIRM_REQUEST, EventType.USER_INPUT_REQUEST):
                active_info["waiting_type"] = "confirm" if event.type == EventType.TOOL_CONFIRM_REQUEST else "input"
                active_info["waiting_since"] = now
                active_info["tool_name"] = event.data.get("tool_name", "")
            elif event.type in (EventType.TOOL_CALL, EventType.SUB_AGENT_TOOL_CALL):
                active_info["tool_name"] = event.data.get("name", "")
                active_info["updated_at"] = now
            elif event.type == EventType.AGENT_ERROR:
                context.metadata["_agent_error"] = True
                active_info["has_error"] = True
                request.app.state.agent_attention[session_id] = {
                    "user_id": user.id,
                    "status": "error",
                    "updated_at": now,
                }

        if event.type in _THROTTLED_EVENTS:
            if now < _throttle_until.get(event.type, 0):
                return
            _throttle_until[event.type] = now + _THROTTLE_INTERVAL

        forward_to_global = event.type not in _SKIP_GLOBAL_EVENTS
        is_critical = event.type in _CRITICAL_EVENT_TYPES

        stale = []
        for qid, entry in list(queues.items()):
            # Parse queue entry — supports old (2/3-tuple) and new (4-tuple) formats
            if isinstance(entry, tuple) and len(entry) >= 4:
                sid, critical_q, stream_q, ts = entry
            elif isinstance(entry, tuple) and len(entry) == 3:
                sid, q, ts = entry
                critical_q = stream_q = q
            elif isinstance(entry, tuple) and len(entry) == 2:
                sid, q = entry
                critical_q = stream_q = q
                ts = now
            else:
                continue

            if now - ts > 60:
                stale.append(qid)
                continue
            if sid != session_id:
                continue

            if is_critical:
                # Critical events → unbounded queue, never dropped
                await critical_q.put(event)
            else:
                try:
                    stream_q.put_nowait(event)
                except asyncio.QueueFull:
                    logger.warning(
                        "_sse_forward: stream queue full session=%s event=%s → dropped (TEXT_DONE will fix tail)",
                        session_id[:8],
                        event.type,
                    )

            queues[qid] = (sid, critical_q, stream_q, now)
        for qid in stale:
            queues.pop(qid, None)

        if forward_to_global:
            global_queues = getattr(request.app.state, "global_event_queues", {})
            gstale = []
            for gqid, (gq, gts) in list(global_queues.items()):
                if now - gts > 120:
                    gstale.append(gqid)
                    continue
                global_event = AgentEvent(
                    type=event.type,
                    data={
                        **event.data,
                        "session_id": event.data.get("sub_agent_id", session_id),
                    },
                    timestamp=event.timestamp,
                )
                try:
                    gq.put_nowait(global_event)
                except asyncio.QueueFull:
                    pass
            for gqid in gstale:
                global_queues.pop(gqid, None)

        if now - _fwd_last_log > 5:
            qsizes = []
            for entry in queues.values():
                if isinstance(entry, tuple) and len(entry) >= 4:
                    s, cq, sq, *_ = entry
                    if s == session_id:
                        qsizes.append(f"c={cq.qsize()}/s={sq.qsize()}")
                elif isinstance(entry, tuple) and len(entry) >= 2:
                    s, q, *_ = entry
                    if s == session_id:
                        qsizes.append(str(q.qsize()))
            logger.info(
                "_sse_forward: %d events in %.0fs session=%s queues=%d qsizes=%s",
                _fwd_count,
                now - _fwd_last_log,
                session_id[:8],
                sum(
                    1
                    for entry in queues.values()
                    if (isinstance(entry, tuple) and len(entry) >= 2 and entry[0] == session_id)
                ),
                qsizes,
            )
            _fwd_count = 0
            _fwd_last_log = now

        if now - _last_stale_cleanup > 30:
            _last_stale_cleanup = now
            await asyncio.sleep(0)

    context.event_bus.subscribe(_sse_forward)

    import base64 as _b64
    import re as _re

    async def _on_browser_screenshot(event: AgentEvent):
        if event.type != EventType.TOOL_RESULT:
            return
        name = event.data.get("name", "")
        if not name.startswith("browser_"):
            return
        result = str(event.data.get("result", "") or "")
        match = _re.search(r"([/\w]+crabagent_screenshots/[a-f0-9_]+\.png)", result)
        if not match:
            return
        path = match.group(1)
        try:
            with open(path, "rb") as f:
                b64 = _b64.b64encode(f.read()).decode()
            data_url = f"data:image/png;base64,{b64}"
        except Exception:
            return
        await context.event_bus.emit(
            AgentEvent(
                type=EventType.SCREENSHOT,
                data={"image": data_url, "tool": name},
            )
        )

    context.event_bus.subscribe(_on_browser_screenshot)

    # ── image_generate → screenshot events (show generated images inline) ──
    # Listen for MESSAGE_CREATED (tool result) instead of TOOL_RESULT because
    # TOOL_RESULT carries a truncated preview (2k chars) while MESSAGE_CREATED
    # carries the full result (up to 20k chars).  The full result is needed to
    # reliably parse the JSON with image paths.
    async def _on_image_generated(event: AgentEvent):
        if event.type != EventType.MESSAGE_CREATED:
            return
        msg = event.data.get("message", {})
        if msg.get("role") != "tool":
            return
        if msg.get("name") not in {"image_generate", "image_edit"}:
            return
        result = str(msg.get("content", "") or "")
        if not result:
            return
        try:
            import json as _json

            parsed = _json.loads(result)
        except Exception:
            logger.debug("image_generate: failed to parse tool result JSON")
            return
        image_entries = parsed.get("images", []) if isinstance(parsed, dict) else []
        for entry in image_entries:
            path = entry.get("path", "")
            if not path:
                continue
            try:
                with open(path, "rb") as f:
                    b64 = _b64.b64encode(f.read()).decode()
                # Guess mime from extension
                ext = path.rsplit(".", 1)[-1].lower() if "." in path else "png"
                mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif", "avif": "avif"}
                mime = mime_map.get(ext, "png")
                data_url = f"data:image/{mime};base64,{b64}"
            except Exception:
                logger.debug("image_generate: failed to read image file %s", path, exc_info=True)
                continue
            # Emit SCREENSHOT for live SSE display
            await context.event_bus.emit(
                AgentEvent(
                    type=EventType.SCREENSHOT,
                    data={"image": data_url, "tool": msg.get("name", "image_generate")},
                )
            )
            # Also emit MESSAGE_CREATED so screenshot is persisted to DB.
            # Store file path (not base64) to keep DB small; frontend loads
            # the image via /files/image API on session reload.
            await context.event_bus.emit(
                AgentEvent(
                    type=EventType.MESSAGE_CREATED,
                    data={
                        "message": {
                            "role": "screenshot",
                            "content": path,
                            "agent": msg.get("agent", "default"),
                        }
                    },
                )
            )

    context.event_bus.subscribe(_on_image_generated)

    # Always install the confirm callback for interactive (desktop) prompts.
    # The global settings.auto_approve_tools is a process-wide singleton that
    # can be temporarily set to True by concurrent scheduled tasks.  Those
    # automated paths build their own AgentContext without a confirm_callback,
    # so they do not need the global flag at all.
    # Checking it here caused a race condition where the desktop UI lost its
    # authorization cards whenever a background task was running.
    from crabagent.serve.api.confirm import request_confirmation

    async def _serve_confirm(tool_name: str, args: dict, diff_preview: dict | None = None) -> bool:
        future = await request_confirmation(
            context.event_bus,
            session_id,
            tool_name,
            args,
            user_id=user.id,
            diff_preview=diff_preview,
        )
        try:
            return await asyncio.wait_for(future, timeout=120.0)
        except TimeoutError:
            return False

    context.confirm_callback = _serve_confirm

    from crabagent.serve.api.input import request_user_input

    async def _serve_ask(
        question: str, options: list[str] | None = None, multiple: bool = False
    ) -> str:
        future = await request_user_input(
            context.event_bus, session_id, question, options=options, multiple=multiple, user_id=user.id
        )
        try:
            return await asyncio.wait_for(future, timeout=300.0)
        except TimeoutError:
            return ""

    context.ask_callback = _serve_ask

    # v0.9 — attach middleware chain (compress + reflect + title)
    try:
        from crabagent.core.agent.middlewares import MiddlewareChain
        from crabagent.core.agent.middlewares.compress_middleware import CompressMiddleware
        from crabagent.core.agent.middlewares.reflect_middleware import ReflectMiddleware
        from crabagent.core.agent.middlewares.title_middleware import TitleMiddleware

        context.middlewares = MiddlewareChain([CompressMiddleware(), ReflectMiddleware(), TitleMiddleware()])
    except Exception:
        logger.debug("Failed to attach middleware chain", exc_info=True)

    @context.tool_registry.register(
        name="ask_question",
        description=(
            "Ask the user a question and get their response. "
            "Use when you need clarification, more information, "
            "or a decision from the user before proceeding."
        ),
        parameters={
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "The question to ask the user",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of choices for the user to pick from",
                },
                "multiple": {
                    "type": "boolean",
                    "description": (
                        "Set true to let the user select several options at once. "
                        "The answer is then the selected options joined by ', '."
                    ),
                },
            },
            "required": ["question"],
        },
    )
    async def ask_question(
        question: str, options: list[str] | None = None, multiple: bool = False, context=None
    ) -> str:
        cb = getattr(context, "ask_callback", None)
        if not cb:
            return "Error: no ask callback available"
        return await cb(question, options, multiple)

    async def _run():
        t0 = time.time()
        try:
            if not conv.title and not conv.auto_titled:
                # First message: show a short placeholder now and have the model
                # write the real title in the background while the agent works.
                from crabagent.core.agent.middlewares.title_middleware import (
                    provisional_title,
                    schedule_title,
                    title_event_emitter,
                    title_source_text,
                )

                placeholder = provisional_title(req.message or "")
                if placeholder:
                    try:
                        async with async_session_factory() as title_db:
                            await conv_svc.update_conversation(title_db, session_id, title=placeholder)
                        schedule_title(
                            session_id=session_id,
                            user_text=title_source_text(req.message or ""),
                            model=context.model or "",
                            provider_name=context.provider_name,
                            expected_title=placeholder,
                            on_title=title_event_emitter(context, session_id),
                        )
                    except Exception:
                        logger.debug("Could not start titling session %s", session_id, exc_info=True)
            agent_query: str | list[dict] = content_blocks if req.images or local_images else req.message
            await run_agent(context, agent_query)
        except asyncio.CancelledError:
            # The finally block still emits AGENT_END for cleanup and stats.
            # Preserve the abort outcome so lifecycle listeners do not mistake
            # that terminal event for a successful completion.
            context.metadata["_run_cancelled"] = True
            logger.info("Agent task cancelled for session %s", session_id)
        except Exception as exc:
            context.metadata["_run_error"] = True
            logger.exception("Agent task failed for session %s", session_id)
            # Never let a failure reach the UI as a bare "Request failed": the
            # user needs the provider, the model and what to do about it.
            await _report_run_failure(context, exc)
        finally:
            context.metadata["_accept_followups"] = False
            # Keep accepted follow-ups in history even if the run was aborted or failed.
            from crabagent.core.agent.loop import _consume_user_messages

            await _consume_user_messages(context)
            elapsed = round(time.time() - t0, 1)
            context.metadata["_run_elapsed"] = elapsed
            resolved_model = context.metadata.get("resolved_model", context.model or "")
            resolved_provider = context.metadata.get("resolved_provider", "")

            browser_mgr = context.metadata.get("_browser_manager")
            if browser_mgr:
                try:
                    await browser_mgr.close()
                except Exception:
                    pass

            stats_data = {
                "elapsed_seconds": elapsed,
                "model": resolved_model,
                "tokens": context.visible_tokens,
                # ── Cumulative token consumption (this run) ──
                "total_prompt": context.accumulated_prompt,
                "total_cached": context.accumulated_cached,
                "total_non_cached": context.accumulated_non_cached,
                "total_completion": context.accumulated_completion,
                "total_reasoning": context.accumulated_reasoning,
                "total_consumed": context.accumulated_total_consumed,
                "iterations": context.iteration,
            }

            import json

            try:
                await context.event_bus.emit(
                    AgentEvent(
                        type=EventType.MESSAGE_CREATED,
                        data={
                            "message": {
                                "role": "stats",
                                "content": json.dumps(stats_data, ensure_ascii=False),
                            }
                        },
                    )
                )
            except Exception:
                logger.warning("Failed to emit stats event", exc_info=True)

            try:
                await context.event_bus.emit(
                    AgentEvent(
                        type=EventType.AGENT_END,
                        data={
                            **stats_data,
                            "cancelled": bool(context.metadata.get("_run_cancelled")),
                            "reason": context.metadata.get("_cancel_reason", ""),
                        },
                    )
                )
            except Exception:
                logger.warning("Failed to emit AGENT_END event", exc_info=True)

            await persistence.finalize()

            # Compression persistence is now handled inline by compress.py
            # (persist_compression), so no post-run batch persist is needed.

            # ── Persist per-iteration token usage to DB ──
            # Most records were already flushed incrementally on ITERATION_END
            # (_usage_flush_handler); this only writes the remaining tail.
            if context.usage_records[usage_flushed:]:
                try:
                    from crabagent.core.database import token_usage_batch_create

                    records = [
                        {
                            "user_id": user.id,
                            "session_id": session_id,
                            "agent_name": context.current_agent,
                            "model": resolved_model,
                            "provider": resolved_provider,
                            "branch_id": active_branch,
                            **r,
                        }
                        for r in context.usage_records[usage_flushed:]
                    ]
                    await token_usage_batch_create(records)
                except Exception:
                    logger.debug("Failed to write token_usage", exc_info=True)

            # ── Persist execution spans to DB ──
            if context.spans:
                try:
                    from crabagent.core.database import execution_span_batch_create
                    from crabagent.core.observability.spans import export_spans

                    run_id = context.metadata.get("_run_id", "")
                    if run_id:
                        await execution_span_batch_create(session_id, run_id, export_spans(context))
                except Exception:
                    logger.debug("Failed to write execution_spans", exc_info=True)

            _contexts.pop(session_id, None)
            _tasks.pop(session_id, None)
            _session_locks.pop(session_id, None)
            # A switch_agent hands the chat over: start the new agent's turn now
            # (as a goal continuation would) instead of waiting for the user.
            switched = context.metadata.get("_switched_to")
            if (
                switched
                and not context.metadata.get("_run_error")
                and not context.metadata.get("_agent_error")
                and not context.metadata.get("_run_cancelled")
            ):
                depth = _next_handover_depth(session_id, req.message)
                if depth is not None:
                    from crabagent.core.goals.scheduler import schedule_goal_continuation

                    handover = _handover_request(req, switched)

                    async def _continue_handover() -> None:
                        _remember_handover_depth(session_id, depth)
                        async with async_session_factory() as handover_db:
                            await prompt_async(session_id, handover, request, user, handover_db)

                    schedule_goal_continuation(session_id, _continue_handover)
            try:
                from crabagent.core.goals.scheduler import schedule_goal_continuation
                from crabagent.core.goals.service import (
                    account_goal_usage,
                    automatic_completion_evidence,
                    checkpoint_goal,
                    finalization_checkpoint,
                    get_current_goal,
                    goal_to_dict,
                )

                async with async_session_factory() as goal_db:
                    goal = await get_current_goal(goal_db, session_id)
                    if goal:
                        if _should_finalize_goal(context.metadata):
                            final_reply = context.messages[-1].get("content", "")
                            evidence = automatic_completion_evidence(final_reply)
                            if evidence:
                                from crabagent.core.goals.service import update_goal

                                await update_goal(goal_db, goal, status="complete", evidence=evidence)
                            else:
                                summary, next_step = finalization_checkpoint(final_reply)
                                await checkpoint_goal(goal_db, goal, summary, next_step)
                        limited = (
                            False
                            if goal.status == "complete"
                            else await account_goal_usage(goal_db, goal, context.accumulated_total_consumed)
                        )
                        await goal_db.commit()
                        snapshot = goal_to_dict(goal)
                    else:
                        limited = False
                        snapshot = None

                if snapshot:
                    event_type = EventType.GOAL_STATUS_CHANGED if limited else EventType.GOAL_UPDATED
                    await context.event_bus.emit(AgentEvent(type=event_type, data={"goal": snapshot}))

                # The next turn is a separate prompt task so cancellation, SSE,
                # and normal session locking continue to work as usual.
                # A failed turn must be terminal for automatic continuation. In
                # particular, an authentication failure cannot be fixed by retrying
                # the same prompt and would otherwise repeat the error indefinitely.
                if _should_continue_goal(snapshot, context.metadata):

                    async def _continue_goal() -> None:
                        try:
                            async with async_session_factory() as goal_check_db:
                                current_goal = await get_current_goal(goal_check_db, session_id)
                                if (
                                    not current_goal
                                    or current_goal.status != "active"
                                    or not current_goal.auto_continue
                                ):
                                    return
                                latest_snapshot = goal_to_dict(current_goal)
                            follow_up = _goal_continuation_request(req)
                            await context.event_bus.emit(
                                AgentEvent(type=EventType.GOAL_CONTINUATION_STARTED, data={"goal": latest_snapshot})
                            )
                            async with async_session_factory() as continue_db:
                                await prompt_async(session_id, follow_up, request, user, continue_db)
                        except Exception:
                            logger.exception("Failed to start goal continuation for session %s", session_id)

                    if schedule_goal_continuation(session_id, _continue_goal):
                        await context.event_bus.emit(
                            AgentEvent(
                                type=EventType.GOAL_CONTINUATION_SCHEDULED, data={"goal": snapshot, "delay_seconds": 2}
                            )
                        )
            except Exception:
                logger.exception("Failed to update goal usage for session %s", session_id)
            request.app.state.active_agents.pop(session_id, None)
            if (
                not context.metadata.get("_run_error")
                and not context.metadata.get("_agent_error")
                and not context.metadata.get("_run_cancelled")
            ):
                request.app.state.agent_attention[session_id] = {
                    "user_id": user.id,
                    "status": "completed",
                    "updated_at": time.time(),
                }

            try:
                async with async_session_factory() as update_db:
                    await conv_svc.update_conversation(
                        update_db,
                        session_id,
                        model=resolved_model,
                        provider=resolved_provider,
                        tokens=context.total_tokens,
                    )
            except Exception:
                logger.exception("Failed to update conversation")

    context.metadata["_accept_followups"] = True
    # This handler emits the terminal AGENT_END itself (stats + persistence in
    # the finally block), so the loop must not emit a second one on a
    # cooperative cancel.
    context.metadata["_caller_emits_agent_end"] = True
    _contexts[session_id] = context
    task = asyncio.create_task(_run())
    _tasks[session_id] = task

    request.app.state.active_agents[session_id] = {
        "user_id": user.id,
        "model": req.model or conv.model or "",
        "status": "running",
        "updated_at": time.time(),
        "started_at": time.time(),
    }

    return {"status": "processing", "session_id": session_id}


@router.post("/sessions/{session_id}/abort")
async def abort_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_owned_conversation(db, session_id, user)

    task = _tasks.get(session_id)
    if not task or task.done():
        raise HTTPException(status_code=404, detail="No running task for this session")

    # Cooperative stop first: the loop finishes the tool it is running, keeps
    # what already happened in history, and emits AGENT_END(cancelled=True)
    # itself. Hard task cancellation remains the fallback for a run that does
    # not reach a checkpoint in time.
    context = _contexts.get(session_id)
    if context is not None:
        context.metadata["_cancel_reason"] = "Stopped by user"
        context.request_cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            logger.info("Cooperative stop for session %s timed out; cancelling the task", session_id)
            task.cancel()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("Agent task ended with an error during abort for %s", session_id, exc_info=True)
    else:
        task.cancel()
    try:
        from crabagent.core.goals.scheduler import cancel_goal_continuation

        cancel_goal_continuation(session_id)
    except Exception:
        pass
    try:
        from crabagent.core.goals.service import get_current_goal, update_goal

        goal = await get_current_goal(db, session_id)
        if goal:
            await update_goal(db, goal, status="paused", stop_reason="Paused by user")
            await db.commit()
    except Exception:
        logger.warning("Failed to pause goal after abort for session %s", session_id, exc_info=True)
    return {"status": "aborted", "session_id": session_id}
