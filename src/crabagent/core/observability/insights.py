"""Automated insight analysis for execution spans.

After a run completes, this module analyses the span tree to detect:
  - Tool call loops (same tool called many times)
  - Token hotspots (one LLM call dominates token usage)
  - Context bloat (prompt tokens growing rapidly across iterations)
  - Error concentration (multiple failed tool calls)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def analyze_spans(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Analyze a flat list of spans from a single run and return insights.

    Args:
        spans: Flat list of span dicts (same format as DB rows).

    Returns:
        List of insight dicts with keys: type, severity, title, detail, span_ids.
    """
    if not spans:
        return []

    insights: list[dict[str, Any]] = []

    # Separate by type
    llm_spans = [s for s in spans if s.get("span_type") == "llm_call"]
    tool_spans = [s for s in spans if s.get("span_type") == "tool_call"]

    # ── 1. Tool call loop detection ──
    tool_counts: dict[str, list[dict]] = {}
    for s in tool_spans:
        name = s.get("name", "")
        tool_counts.setdefault(name, []).append(s)

    for name, group in tool_counts.items():
        if len(group) >= 5:
            insights.append({
                "type": "tool_loop",
                "severity": "warning",
                "title": f"Tool '{name}' was called {len(group)} times",
                "detail": (
                    "The agent may be calling the same tool in a loop."
                    "Consider improving the prompt or adding stop conditions."
                ),
                "span_ids": [s["id"] for s in group if "id" in s],
            })

    # ── 2. Token hotspot: one LLM call dominates prompt token usage ──
    if llm_spans:
        total_prompt = sum(s.get("prompt_tokens", 0) for s in llm_spans)
        if total_prompt > 0:
            max_span = max(llm_spans, key=lambda s: s.get("prompt_tokens", 0))
            max_prompt = max_span.get("prompt_tokens", 0)
            if max_prompt > total_prompt * 0.6 and max_prompt > 1000:
                pct = round(max_prompt / total_prompt * 100) if total_prompt else 0
                insights.append({
                    "type": "token_hotspot",
                    "severity": "info",
                    "title": (
                        f"A single LLM call used {max_prompt:,} prompt tokens"
                        f" ({pct}% of total)"
                    ),
                    "detail": (
                        "Context may have grown too long."
                        "Consider enabling context compression or splitting the task to lower per-call cost."
                    ),
                    "span_id": max_span.get("id"),
                })

    # ── 3. Context bloat: prompt tokens growing rapidly ──
    if len(llm_spans) >= 3:
        sorted_llm = sorted(llm_spans, key=lambda s: s.get("seq", 0))
        prompts = [s.get("prompt_tokens", 0) for s in sorted_llm]
        # Check if growth rate exceeds 30% per iteration
        growing = True
        for i in range(len(prompts) - 1):
            if prompts[i] == 0 or prompts[i + 1] < prompts[i] * 1.3:
                growing = False
                break
        if growing and prompts[0] > 0:
            growth = round(prompts[-1] / prompts[0], 1) if prompts[0] else 0
            insights.append({
                "type": "context_bloat",
                "severity": "warning",
                "title": "Context growing fast",
                "detail": (
                    f"Prompt tokens grew from {prompts[0]:,} to "
                    f"{prompts[-1]:,} ({len(prompts)} rounds, "
                    f"about {growth}x)."
                ),
            })

    # ── 4. Error concentration ──
    error_tools = [s for s in tool_spans if s.get("status") == "error"]
    if error_tools:
        error_names = {}
        for s in error_tools:
            name = s.get("name", "unknown")
            error_names[name] = error_names.get(name, 0) + 1
        detail_parts = [f"{name}: {count} calls" for name, count in list(error_names.items())[:3]]
        insights.append({
            "type": "tool_errors",
            "severity": "error",
            "title": f"{len(error_tools)} tool calls failed",
            "detail": "; ".join(detail_parts),
            "span_ids": [s["id"] for s in error_tools if "id" in s],
        })

    # ── 5. Slow tool calls ──
    slow_tools = [s for s in tool_spans if s.get("duration_ms", 0) > 30_000]
    if slow_tools:
        slow_names = {}
        for s in slow_tools:
            name = s.get("name", "unknown")
            old = slow_names.get(name, 0)
            slow_names[name] = max(old, s.get("duration_ms", 0))
        detail_parts = [
            f"{name}: {ms/1000:.0f}s" for name, ms in list(slow_names.items())[:3]
        ]
        insights.append({
            "type": "slow_tools",
            "severity": "info",
            "title": f"{len(slow_tools)} tool calls took over 30s",
            "detail": "; ".join(detail_parts),
            "span_ids": [s["id"] for s in slow_tools if "id" in s],
        })

    return insights


