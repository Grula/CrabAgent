"""Conversation titles: a short placeholder at once, a model-written title soon after.

Flow for the web UI:

1. When the first message of a conversation arrives, the server stores a
   placeholder (``provisional_title``: the start of the request, without
   internal delegation syntax or attachment lists) and calls
   ``schedule_title`` with that message, so the real title is written while
   the agent is still working.
2. ``TitleMiddleware.on_conversation_end`` is the fallback for paths that did
   not schedule a title (CLI, TUI, a failed early attempt). It uses the first
   user message and the first assistant reply.

A title is only ever written over a default or placeholder title, and only if
the title did not change while the model was writing it, so a conversation
the user renamed is never overwritten. ``auto_titled`` marks finished titles.

Everything here is fail-safe: DB or LLM errors are logged and swallowed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable

import litellm

logger = logging.getLogger(__name__)

_TITLE_PROMPT = (
    "Write a title of 3 to 7 words for the conversation that starts below. Name its specific topic or "
    "task (for example \"Audit project security and structure\" or \"Fix flaky background test\"), not "
    "generic words like \"Question\" or \"Help request\". Use the language of the user's message. "
    "Output only the title: no quotes, no prefix, no final punctuation.\n\n"
    "User: {user}\n"
    "{assistant_block}"
    "\nTitle:"
)

# Enough room for models that reason before answering.
_TITLE_MAX_TOKENS = 400
_MAX_TITLE_CHARS = 80
_PROVISIONAL_CHARS = 50

# Sessions with a title request in flight (one per session at a time).
_IN_FLIGHT: set[str] = set()

_DELEGATE_RE = re.compile(r"^\s*\[delegate_(task|parallel)\]", re.IGNORECASE)
_ATTACHMENTS_MARKER = "📎 Attachments:"


# ---------------------------------------------------------------------------
# What to title from
# ---------------------------------------------------------------------------


def title_source_text(message: str) -> str:
    """The user's actual request, without the UI's internal wrappers.

    * ``[delegate_task] agent_name="x" task="..."`` → the task (or the
      "Original request" the UI appends after it)
    * ``[delegate_parallel] tasks=[...]`` → the tasks joined with "; "
    * a trailing "📎 Attachments:" block → removed
    """
    text = (message or "").strip()
    if not text:
        return ""

    if _DELEGATE_RE.match(text):
        original = re.search(r"Original request:\s*(.+)", text, re.DOTALL)
        if original:
            text = original.group(1)
            text = re.sub(r"(?<!\S)@[\w-]+", "", text)
        else:
            task = re.search(r'task="(.*?)"\s*$', text.split("\n", 1)[0])
            tasks = re.search(r"tasks=(\[.*\])", text.split("\n", 1)[0])
            if task:
                text = task.group(1)
            elif tasks:
                try:
                    items = json.loads(tasks.group(1))
                    text = "; ".join(str(item.get("task", "")) for item in items if isinstance(item, dict))
                except (ValueError, TypeError):
                    text = ""

    if _ATTACHMENTS_MARKER in text:
        text = text.split(_ATTACHMENTS_MARKER, 1)[0]
    return re.sub(r"\s+", " ", text).strip()


def provisional_title(message: str) -> str:
    """Placeholder shown until the model-written title arrives."""
    text = title_source_text(message)
    if len(text) <= _PROVISIONAL_CHARS:
        return text
    cut = text[:_PROVISIONAL_CHARS].rsplit(" ", 1)[0] or text[:_PROVISIONAL_CHARS]
    return cut.rstrip(" ,.;:") + "…"


def _legacy_placeholders(message: str) -> set[str]:
    """Placeholder formats written by earlier versions (web, CLI and TUI)."""
    raw = (message or "").strip()
    out = {raw[:50], raw[:50] + "...", raw[:50] + "…"}
    return {item for item in out if item}


def is_replaceable_title(title: str, first_user_message: str) -> bool:
    """True when ``title`` is a default or a placeholder derived from the first message."""
    if _is_default_title(title):
        return True
    title = title.strip()
    if not first_user_message:
        return False
    return title == provisional_title(first_user_message) or title in _legacy_placeholders(first_user_message)


# ---------------------------------------------------------------------------
# Writing the title
# ---------------------------------------------------------------------------

TitleListener = Callable[[str], Awaitable[None]]


def schedule_title(
    *,
    session_id: str,
    user_text: str,
    assistant_text: str = "",
    model: str,
    provider_name: str | None,
    expected_title: str,
    on_title: TitleListener | None = None,
) -> bool:
    """Start writing a title in the background. Returns False if one is already running."""
    if not session_id or not user_text or not model or session_id in _IN_FLIGHT:
        return False
    _IN_FLIGHT.add(session_id)

    async def _run() -> None:
        try:
            await _write_title(
                session_id=session_id,
                user_text=user_text,
                assistant_text=assistant_text,
                model=model,
                provider_name=provider_name,
                expected_title=expected_title,
                on_title=on_title,
            )
        finally:
            _IN_FLIGHT.discard(session_id)

    asyncio.create_task(_run())
    return True


async def _write_title(
    *,
    session_id: str,
    user_text: str,
    assistant_text: str,
    model: str,
    provider_name: str | None,
    expected_title: str,
    on_title: TitleListener | None,
) -> None:
    try:
        title = await generate_title(user_text, assistant_text, model, provider_name)
        if not title:
            return

        from sqlalchemy import update

        from crabagent.core.database import Conversation, async_session_factory

        async with async_session_factory() as db:
            result = await db.execute(
                update(Conversation)
                .where(
                    Conversation.session_id == session_id,
                    Conversation.auto_titled.is_not(True),
                    # Renamed meanwhile (by the user or another writer): keep that name.
                    Conversation.title == expected_title,
                )
                .values(title=title, auto_titled=True)
            )
            await db.commit()
        if not result.rowcount:
            return
        logger.info("Auto-titled conversation %s: %r", session_id, title)
        if on_title is not None:
            try:
                await on_title(title)
            except Exception:
                logger.debug("Title listener failed", exc_info=True)
    except Exception:
        logger.debug("Writing a conversation title failed", exc_info=True)


async def generate_title(user_text: str, assistant_text: str, model: str, provider_name: str | None) -> str:
    """Ask the model for a title; returns "" when it gives nothing usable."""
    llm_params = await _resolve_llm_params(provider_name)
    if llm_params is None:
        return ""
    try:
        from crabagent.core.provider_store import get_default_provider, get_provider, resolve_model_for_provider

        provider = await get_provider(provider_name) if provider_name else await get_default_provider()
        if provider:
            model = resolve_model_for_provider(provider, model)
    except Exception:
        pass

    assistant_block = f"Assistant: {assistant_text[:400]}\n" if assistant_text else ""
    prompt = _TITLE_PROMPT.format(user=user_text[:600], assistant_block=assistant_block)
    response = await litellm.acompletion(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        max_tokens=_TITLE_MAX_TOKENS,
        temperature=0.2,
        **llm_params,
    )
    raw = ""
    if getattr(response, "choices", None):
        raw = (getattr(response.choices[0].message, "content", "") or "").strip()
    # Never fall back to reasoning text: it is the model thinking, not a title.
    return _finalize_title(raw)


def _finalize_title(raw: str) -> str:
    lines = [line for line in (raw or "").splitlines() if line.strip()]
    if not lines:
        return ""
    title = _clean_title(lines[0])
    if len(title) < 2:
        return ""
    if len(title) > _MAX_TITLE_CHARS:
        title = title[:_MAX_TITLE_CHARS].rsplit(" ", 1)[0].rstrip(" ,.;:") or title[:_MAX_TITLE_CHARS]
    return title


# ---------------------------------------------------------------------------
# Middleware (fallback after the first exchange)
# ---------------------------------------------------------------------------


class TitleMiddleware:
    name = "title"

    async def on_conversation_start(self, context) -> None:  # noqa: ARG002
        return None

    async def before_llm_call(self, context, messages: list[dict]) -> list[dict]:  # noqa: ARG002
        return messages

    async def on_conversation_end(self, context) -> None:
        session_id = context.metadata.get("session_id", "")
        if not session_id or session_id in _IN_FLIGHT:
            return

        first_user = _first_user_text(context.messages)
        user_text = title_source_text(first_user)
        if not user_text:
            return

        # Only early in a conversation. Count user turns, not messages: a single
        # turn with tool calls easily has dozens of messages.
        if sum(1 for msg in context.messages if msg.get("role") == "user") > 3:
            return

        model = context.metadata.get("_resolved_model") or context.model or ""
        if not model:
            return

        try:
            from sqlalchemy import select

            from crabagent.core.database import Conversation, async_session_factory

            async with async_session_factory() as db:
                row = await db.execute(
                    select(Conversation.title, Conversation.auto_titled).where(Conversation.session_id == session_id)
                )
                existing = row.first()
            if not existing:
                return
            current_title, auto_titled = existing
            current_title = current_title or ""
            if auto_titled or not is_replaceable_title(current_title, first_user):
                return
        except Exception:
            logger.debug("TitleMiddleware DB check failed", exc_info=True)
            return

        schedule_title(
            session_id=session_id,
            user_text=user_text,
            assistant_text=_first_assistant_text(context.messages),
            model=model,
            provider_name=context.provider_name,
            expected_title=current_title,
            on_title=title_event_emitter(context, session_id),
        )


def title_event_emitter(context, session_id: str) -> TitleListener | None:
    """Tell connected browsers about a new title through the run's event bus."""
    bus = getattr(context, "event_bus", None)
    if bus is None:
        return None

    async def _emit(title: str) -> None:
        from crabagent.core.event import AgentEvent, EventType

        await bus.emit(AgentEvent(type=EventType.SESSION_TITLE, data={"session_id": session_id, "title": title}))

    return _emit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _first_user_text(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "").strip()
                    if text:
                        return text
    return ""


def _first_assistant_text(messages: list[dict]) -> str:
    for msg in messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip()
    return ""


def _is_default_title(title: str) -> bool:
    """A 'default' title is one that the system auto-assigned at creation,
    e.g. 'Conversation 1', 'New Chat', 'Untitled'."""
    if not title:
        return True
    t = title.strip().lower()
    defaults = {
        "new",
        "new conversation",
        "new chat",
        "untitled",
        "conversation",
    }
    if t in defaults:
        return True
    # 'conversation N' / 'chat N' patterns
    if re.match(r"^(conversation|chat)\s*#?\d+$", t):
        return True
    return False


def _clean_title(title: str) -> str:
    t = title.strip().strip("\"'`*#").strip()
    # Strip leading "Title:" if the model echoed it
    t = re.sub(r"^title\s*[:：]\s*", "", t, flags=re.IGNORECASE)
    t = t.strip("\"'`*").strip()
    # Strip trailing punctuation that's just visual noise
    t = re.sub(r"[.。!！?？]+$", "", t)
    # Collapse whitespace
    t = re.sub(r"\s+", " ", t)
    return t.strip()


async def _resolve_llm_params(provider_name: str | None) -> dict | None:
    try:
        from crabagent.core.provider_store import get_default_provider, get_provider

        provider = await get_provider(provider_name) if provider_name else await get_default_provider()
    except Exception:
        provider = None
    if not provider:
        return None
    from crabagent.core.provider_store import resolve_litellm_params

    return await resolve_litellm_params(provider)


__all__ = [
    "TitleMiddleware",
    "generate_title",
    "is_replaceable_title",
    "provisional_title",
    "schedule_title",
    "title_event_emitter",
    "title_source_text",
]
