"""Regression tests for the Electron + macOS + i18n removal refactor.

Derived from the Architect's spec (see shared notes ``removal_spec_electron_macos``
and ``removal_spec_decisions``), NOT from the implementation. Each test encodes a
single spec guarantee so a regression points at exactly one broken promise.

Spec summary being enforced here:

* U1-U3: the Electron desktop shell, the desktop pet and the macOS computer-use
  surface are removed from *both* backend and frontend.
* U4: the collaboration browser (``collab_browser_*``) and the browser-backed
  computer tools (``computer_observe`` / ``computer_act``) are KEPT, because they
  work in Web mode via ``/collab-browser.html`` + the Playwright bridge.
* U6: all Chinese strings are gone; user-facing text is hardcoded English and the
  ``locale`` plumbing no longer changes the output language.

NOTE: every test here is *synchronous* on purpose. ``pyproject.toml`` declares
``asyncio_mode = "auto"`` but ``pytest-asyncio`` is not installed in this
environment, so any ``async def`` test silently fails at setup. Sync tests stay
meaningful regardless of the plugin situation.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
FRONTEND_SRC = REPO_ROOT / "frontend" / "src"

CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# Python string literals (single, double, triple quoted) -- used to distinguish
# "hardcoded user-facing Chinese" from Chinese that only appears in comments.
_PY_STRING_RE = re.compile(
    r'"""(?:.|\n)*?"""'
    r"|'''(?:.|\n)*?'''"
    r'|"(?:[^"\\\n]|\\.)*"'
    r"|'(?:[^'\\\n]|\\.)*'"
)


def _load_full_registry():
    """Import the same modules the server does, then return the live registry.

    ``crabagent.core`` registers the always-on tools; ``serve.api.prompt`` pulls
    in the lazily registered capability-gated ones (browser, collaboration
    browser, computer, scheduled_task, agent, custom tool).
    """
    import crabagent.core  # noqa: F401

    try:
        import crabagent.serve.api.prompt  # noqa: F401
    except Exception:  # pragma: no cover - prompt.py guards its own imports
        pass

    from crabagent.core.agent.tools.registry import registry

    return registry


def _registered_names() -> list[str]:
    registry = _load_full_registry()
    return sorted(t.name for t in registry.list_tools())


# ---------------------------------------------------------------------------
# U1-U3: removal of the Electron shell / pet / macOS computer-use surface
# ---------------------------------------------------------------------------


def test_no_macos_tool_is_registered():
    """No ``macos_*`` tool may remain in the tool registry."""
    names = _registered_names()
    offenders = [n for n in names if "macos" in n.lower()]
    assert offenders == [], f"macOS tools still registered: {offenders}"
    # sanity: the registry is actually populated (guards against a broken import
    # silently making this test vacuous)
    assert len(names) > 20, f"registry looks empty ({len(names)} tools) -- check imports"


def test_macos_computer_tool_module_is_gone():
    """The ``tools/macos_computer`` module must no longer be importable."""
    spec = importlib.util.find_spec("crabagent.core.agent.tools.macos_computer")
    assert spec is None, "crabagent.core.agent.tools.macos_computer is still importable"


def test_electron_desktop_shell_files_are_gone():
    """Electron entrypoints must be deleted, not merely unreferenced."""
    leftovers = [
        path
        for path in (
            REPO_ROOT / "main.js",
            REPO_ROOT / "preload.js",
            REPO_ROOT / "electron",
            SRC_ROOT / "crabagent" / "electron",
        )
        if path.exists()
    ]
    assert leftovers == [], f"Electron shell files still present: {leftovers}"


def test_frontend_macos_and_pet_components_are_gone():
    """The macOS settings panel and the desktop-pet settings panel must be deleted."""
    leftovers = [
        name
        for name in ("MacosComputerPanel.tsx", "PetsSettingsPanel.tsx", "DesktopPet.tsx")
        if (FRONTEND_SRC / "components" / name).exists()
    ]
    assert leftovers == [], f"removed UI components still present: {leftovers}"


def test_no_electron_api_usage_in_frontend():
    """``window.electronAPI`` must have zero references after the shell removal."""
    offenders = []
    for path in list(FRONTEND_SRC.rglob("*.ts")) + list(FRONTEND_SRC.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "electronAPI" in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], f"window.electronAPI still referenced in: {sorted(offenders)}"


def test_settings_page_has_no_pets_or_macos_tabs():
    """``SettingsPage.tsx`` must not define ``pets`` or ``macos`` tabs anymore."""
    text = (FRONTEND_SRC / "pages" / "SettingsPage.tsx").read_text(encoding="utf-8")
    tab_ids = set(re.findall(r'\bid:\s*"([a-zA-Z]+)"', text))
    assert "pets" not in tab_ids, f"'pets' tab still declared (tabs={sorted(tab_ids)})"
    assert "macos" not in tab_ids, f"'macos' tab still declared (tabs={sorted(tab_ids)})"


def test_navbar_has_no_language_switcher():
    """U6 deletes the language switcher wholesale."""
    text = (FRONTEND_SRC / "components" / "NavBar.tsx").read_text(encoding="utf-8")
    assert "LanguageSwitcher" not in text, "NavBar still defines/uses LanguageSwitcher"
    assert "crabagent-language" not in text, "NavBar still persists a language preference"


# ---------------------------------------------------------------------------
# U4: the collaboration browser + web-mode computer tools are KEPT
# ---------------------------------------------------------------------------


def test_collaboration_browser_tools_still_registered():
    """The non-Electron Web-mode collaboration browser must be preserved."""
    names = set(_registered_names())
    expected = {
        "collab_browser_open",
        "collab_browser_observe",
        "collab_browser_click",
        "collab_browser_type",
        "collab_browser_screenshot",
    }
    missing = expected - names
    assert missing == set(), f"collaboration browser tools were removed: {sorted(missing)}"


def test_web_mode_computer_tools_still_registered():
    """``computer_observe`` / ``computer_act`` run on the Playwright bridge and stay."""
    names = set(_registered_names())
    missing = {"computer_observe", "computer_act"} - names
    assert missing == set(), f"web-mode computer tools were removed: {sorted(missing)}"


def test_collaboration_browser_page_and_bridge_exist():
    """The Web fallback page and its bridge service must still exist."""
    assert (REPO_ROOT / "frontend" / "public" / "collab-browser.html").is_file(), (
        "frontend/public/collab-browser.html was deleted -- Web mode collaboration "
        "browser would break"
    )
    assert (
        SRC_ROOT / "crabagent" / "core" / "computer" / "bridge_service.py"
    ).is_file(), "core/computer/bridge_service.py was deleted -- Playwright bridge gone"


# ---------------------------------------------------------------------------
# U6: Chinese fully removed, user-facing text hardcoded English
# ---------------------------------------------------------------------------


def test_project_memory_to_prompt_renders_english_header():
    """``to_prompt()`` must emit the English ``=== Project Context ===`` block."""
    from crabagent.core.project_memory import ProjectProfile

    memory = ProjectProfile(
        workspace=".",
        last_active="2026-01-02",
        tech_stack=["Python", "React"],
        recent_lessons=["use pytest"],
        lesson_count=1,
    )
    rendered = memory.to_prompt()
    assert "=== Project Context ===" in rendered, repr(rendered)
    assert "\u4e0a\u6b21\u6d3b\u8dc3" not in rendered and "\u6280\u672f\u6808" not in rendered, repr(rendered)
    assert not CJK_RE.search(rendered), f"to_prompt() still contains Chinese: {rendered!r}"


def test_project_memory_to_prompt_is_empty_without_data():
    """An empty memory must still render to ``""`` (no stray header)."""
    from crabagent.core.project_memory import ProjectProfile

    memory = ProjectProfile(workspace=".", tech_stack=["(unknown)"])
    assert memory.to_prompt() == ""


def test_scheduled_task_messages_are_english_regardless_of_locale():
    """U6 removes the locale pipeline: output must be English for every locale."""
    import crabagent.core  # noqa: F401
    import crabagent.serve.api.prompt  # noqa: F401

    from crabagent.core.agent.tools import scheduled_task as st

    for locale in ("en", "zh-CN", "zh_CN", "zh"):
        message = st._t("scheduled_task_not_found", locale, id=1)
        assert not CJK_RE.search(message), (
            f"locale={locale!r} still returns Chinese: {message!r} -- "
            "the locale plumbing was not removed"
        )
        assert "1" in message


def test_message_service_task_result_card_is_english():
    """The task-result card injected into the LLM prompt must be English."""
    from crabagent.core.database import Message
    from crabagent.serve.services import message as message_service

    payload = {
        "task_id": 22,
        "title": "Budget report",
        "status": "done",
        "verification_status": "passed",
    }
    msg = Message(
        conversation_id=1,
        sequence=9,
        role="task_result",
        content=json.dumps(payload, ensure_ascii=False),
    )
    rendered = message_service.message_to_dict(msg)["content"]
    assert not CJK_RE.search(rendered), f"task result card still Chinese: {rendered!r}"


def test_no_zh_cn_locale_files_remain():
    """Both the frontend and backend zh-CN dictionaries must be deleted."""
    stale = [
        str(path.relative_to(REPO_ROOT))
        for path in list(SRC_ROOT.rglob("zh-CN*")) + list(FRONTEND_SRC.rglob("zh-CN*"))
        if "__pycache__" not in str(path)
    ]
    assert stale == [], f"zh-CN locale files still present: {sorted(stale)}"


def test_localized_readme_and_changelog_are_deleted():
    """``README.zh-CN.md`` / ``CHANGELOG.zh-CN.md`` are part of U6."""
    stale = [
        name
        for name in ("README.zh-CN.md", "CHANGELOG.zh-CN.md")
        if (REPO_ROOT / name).exists()
    ]
    assert stale == [], f"Chinese docs still present: {stale}"


def test_no_cjk_in_frontend_sources():
    """Zero CJK anywhere under ``frontend/src`` (hardcoded Chinese must be gone).

    One deliberate exception: ``*.test.ts`` / ``*.test.tsx`` are skipped so tests may
    carry arbitrary non-ASCII fixtures without reintroducing shipping Chinese.
    """
    offenders: dict[str, int] = {}
    for pattern in ("*.ts", "*.tsx"):
        for path in FRONTEND_SRC.rglob(pattern):
            if path.name.endswith((".test.ts", ".test.tsx")):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits = len(CJK_RE.findall(text))
            if hits:
                offenders[str(path.relative_to(REPO_ROOT))] = hits
    total = sum(offenders.values())
    top = sorted(offenders.items(), key=lambda kv: -kv[1])[:10]
    assert total == 0, (
        f"{total} CJK characters remain in {len(offenders)} frontend files; "
        f"worst offenders: {top}"
    )


def test_no_cjk_in_python_string_literals():
    """Zero CJK inside Python string literals (comments/docstrings excluded).

    Everything in ``src/`` must be hardcoded English.
    """
    offenders: dict[str, int] = {}
    for path in SRC_ROOT.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = sum(
            1 for match in _PY_STRING_RE.finditer(text) if CJK_RE.search(match.group())
        )
        if hits:
            offenders[str(path.relative_to(REPO_ROOT))] = hits
    total = sum(offenders.values())
    top = sorted(offenders.items(), key=lambda kv: -kv[1])[:10]
    assert total == 0, (
        f"{total} Chinese string literals remain in {len(offenders)} Python files; "
        f"worst offenders: {top}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
