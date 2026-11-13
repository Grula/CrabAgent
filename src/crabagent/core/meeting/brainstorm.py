from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

VALID_VERDICTS = ("agree", "disagree", "ignore")


def _extract_json(raw: str) -> dict:
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _normalize_reactions(items) -> list[dict]:
    reactions = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("idea_index"))
        except (TypeError, ValueError):
            continue
        verdict = str(item.get("verdict") or "").strip().lower()
        if verdict not in VALID_VERDICTS:
            verdict = "ignore"
        reactions.append(
            {
                "idea_index": index,
                "verdict": verdict,
                "comment": str(item.get("comment") or ""),
            }
        )
    return reactions


async def _load_agents(participants: list[str]) -> list:
    from sqlalchemy import select

    from crabagent.core.database import AgentProfile, async_session_factory

    agents = []
    async with async_session_factory() as db:
        for name in participants:
            result = await db.execute(
                select(AgentProfile).where(
                    AgentProfile.name == name,
                    AgentProfile.enabled == True,  # noqa: E712
                )
            )
            profile = result.scalar_one_or_none()
            if profile is not None:
                agents.append(profile)
    return agents


async def _resolve_model(context=None) -> tuple[str, dict]:
    from crabagent.core.provider_store import (
        get_default_provider,
        resolve_litellm_params,
        resolve_model_for_provider,
    )

    model = "gpt-4o"
    llm_params: dict = {}
    try:
        provider = await get_default_provider()
        if provider:
            requested = model
            if context:
                requested = context.metadata.get("model", model)
            model = resolve_model_for_provider(provider, requested)
            llm_params = await resolve_litellm_params(provider)
    except Exception:
        pass
    return model, llm_params


async def brainstorm(
    topic: str,
    participants: list[str],
    user_id: int = 1,
    context=None,
) -> dict:
    if not participants:
        return {"ideas": [], "reactions": []}

    import litellm

    model, llm_params = await _resolve_model(context)
    notes = (getattr(context, "metadata", None) or {}).get("notes", "") if context else ""

    ideas_entries: list[tuple] = []
    for profile in await _load_agents(participants):
        system = (
            f"You are {profile.role}. Goal: {profile.goal}. "
            "Contribute 1-3 ideas. Ideas only — no implementation/plans."
        )
        user = f"Topic: {topic}"
        if notes:
            user += f"\n\nRecent notes:\n{notes}"
        user += '\n\nRespond with JSON only: {"ideas": ["..."]}'
        try:
            response = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.7,
                **llm_params,
            )
            raw = response.choices[0].message.content or "{}"
        except Exception as e:
            logger.error(f"Brainstorm idea pass failed for {profile.name}: {e}")
            continue
        data = _extract_json(raw)
        ideas_entries.append((profile, [str(idea) for idea in (data.get("ideas") or [])]))

    flat = [idea for _, ideas in ideas_entries for idea in ideas]
    listing = "\n".join(f"{i}. {idea}" for i, idea in enumerate(flat))

    reactions_entries = []
    for profile, _ in ideas_entries:
        system = (
            f"You are {profile.role}. Goal: {profile.goal}. "
            "React to every idea with agree, disagree or ignore."
        )
        user = (
            f"Topic: {topic}\n\nIdeas:\n{listing}\n\n"
            'Respond with JSON only: {"reactions": [{"idea_index": 0, '
            '"verdict": "agree", "comment": "..."}]}'
        )
        try:
            response = await litellm.acompletion(
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.7,
                **llm_params,
            )
            raw = response.choices[0].message.content or "{}"
        except Exception as e:
            logger.error(f"Brainstorm reaction pass failed for {profile.name}: {e}")
            continue
        data = _extract_json(raw)
        reactions_entries.append(
            {"agent": profile.name, "reactions": _normalize_reactions(data.get("reactions"))}
        )

    return {
        "ideas": [{"agent": profile.name, "ideas": ideas} for profile, ideas in ideas_entries],
        "reactions": reactions_entries,
    }
