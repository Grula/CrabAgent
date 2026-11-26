"""Default agent for new chats, and built-in agents that stay deleted."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import crabagent.core.agent.agents as agents
import crabagent.core.database as db


@pytest.fixture
async def agents_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(db.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db, "async_session_factory", factory)
    monkeypatch.setattr(agents, "async_session_factory", factory)
    agents.invalidate_cache()
    yield factory
    agents.invalidate_cache()
    await engine.dispose()


async def _add(factory, *rows) -> None:
    async with factory() as session:
        session.add_all(rows)
        await session.commit()


def _profile(name: str, enabled: bool = True, builtin: bool = False) -> db.AgentProfile:
    return db.AgentProfile(user_id=1, name=name, display_name=name.title(), role="r", goal="g",
                           enabled=enabled, is_default=builtin, tool_permissions="{}")


class TestResolveDefaultAgent:
    async def test_unset_means_the_plain_assistant(self, agents_db):
        await _add(agents_db, _profile("architect"))
        assert await agents.resolve_default_agent_name() == "default"

    async def test_configured_agent_is_used(self, agents_db):
        await _add(agents_db, _profile("architect"), db.AppSetting(key="default_agent", value="architect"))
        assert await agents.resolve_default_agent_name() == "architect"

    @pytest.mark.parametrize("value", ["missing", "__default__", "disabled_one", ""])
    async def test_unusable_choices_fall_back(self, agents_db, value):
        await _add(agents_db, _profile("disabled_one", enabled=False), _profile("__default__"),
                   db.AppSetting(key="default_agent", value=value))
        assert await agents.resolve_default_agent_name() == "default"


class TestRemovedBuiltins:
    def test_parse(self):
        assert db.parse_removed_builtin_agents('["coder", "writer"]') == {"coder", "writer"}
        assert db.parse_removed_builtin_agents(None) == set()
        assert db.parse_removed_builtin_agents("not json") == set()
        assert db.parse_removed_builtin_agents('{"a": 1}') == set()

    async def test_seeding_skips_removed_builtins(self, agents_db):
        removed = ["researcher", "analyst"]
        await _add(agents_db, db.AppSetting(key=db.REMOVED_BUILTIN_AGENTS_SETTING, value=json.dumps(removed)))
        await db._ensure_default_agents()
        async with agents_db() as session:
            names = set((await session.execute(select(db.AgentProfile.name))).scalars())
        builtin = {item["name"] for item in db.DEFAULT_AGENTS}
        assert names == builtin - set(removed)

    async def test_seeded_team_gets_its_own_skills(self, agents_db):
        await db._ensure_default_agents()
        async with agents_db() as session:
            rows = {r.name: r for r in (await session.execute(select(db.AgentProfile))).scalars()}
        assert set(rows) == {
            "general", "engineer", "architect", "coder", "tester", "ui_checker", "the_reviewer", "researcher", "scout"
        }
        assert "plan" in json.loads(rows["architect"].skills)
        assert {"write-spec", "write-readme", "project-init", "design-patterns"} <= set(
            json.loads(rows["architect"].skills))
        assert "docx" not in json.loads(rows["architect"].skills)
        assert json.loads(rows["the_reviewer"].skills) == [
            "strict-review", "code-review", "security-review", "dead-code-audit", "design-patterns", "git-workflow",
            "docs-lookup", "run-app"]
        # An empty list means no skills at all (None would mean every skill).
        assert json.loads(rows["scout"].skills) == []

    async def test_seeding_never_changes_an_existing_agents_skills(self, agents_db):
        custom = _profile("architect", builtin=True)
        custom.skills = None
        await _add(agents_db, custom)
        await db._ensure_default_agents()
        async with agents_db() as session:
            row = (await session.execute(select(db.AgentProfile).where(db.AgentProfile.name == "architect"))).scalar_one()
        assert row.skills is None

    def test_every_default_skill_is_bundled(self):
        from pathlib import Path

        from crabagent.core.agent.skill.loader import discover_skills

        bundled = discover_skills([Path(db.__file__).resolve().parents[1] / "skills"])
        for agent in db.DEFAULT_AGENTS:
            missing = set(agent.get("skills", [])) - set(bundled)
            assert not missing, f"{agent['name']} lists skills that are not bundled: {missing}"


class TestDeleteEndpoint:
    async def _delete(self, factory, name: str):
        from crabagent.serve.api.agent import delete_agent_profile

        async with factory() as session:
            return await delete_agent_profile(name, user=None, db=session)

    async def test_deleting_a_builtin_is_remembered_and_clears_the_default(self, agents_db):
        await _add(agents_db, _profile("coder", builtin=True), _profile("architect"),
                   db.AppSetting(key="default_agent", value="coder"))
        assert (await self._delete(agents_db, "coder"))["status"] == "deleted"
        async with agents_db() as session:
            settings = {row.key: row.value for row in (await session.execute(select(db.AppSetting))).scalars()}
            names = set((await session.execute(select(db.AgentProfile.name))).scalars())
        assert names == {"architect"}
        assert json.loads(settings[db.REMOVED_BUILTIN_AGENTS_SETTING]) == ["coder"]
        assert settings["default_agent"] == ""

        # and it stays gone after the startup seeding
        await db._ensure_default_agents()
        async with agents_db() as session:
            assert not (await session.execute(select(db.AgentProfile).where(db.AgentProfile.name == "coder"))).first()

    async def test_internal_profile_cannot_be_deleted(self, agents_db):
        from fastapi import HTTPException

        await _add(agents_db, _profile("__default__", builtin=True))
        with pytest.raises(HTTPException) as err:
            await self._delete(agents_db, "__default__")
        assert err.value.status_code == 403


class TestDefaultTeamCapabilities:
    """The shipped team gets the tools its jobs need, not email or the calendar."""

    def _caps(self, name: str) -> list[str]:
        agent = next(a for a in db.DEFAULT_AGENTS if a["name"] == name)
        return agents.agent_capabilities(agent["tool_permissions"])

    def test_no_default_agent_handles_organiser_or_extra_tools(self):
        unneeded = {"email", "calendar", "scheduled tasks", "images", "computer control", "building tools"}
        for agent in db.DEFAULT_AGENTS:
            caps = self._caps(agent["name"])
            assert not unneeded & set(caps), (agent["name"], caps)
            # observe without act is useless, and its hint claims the agent can drive the browser
            assert agent["tool_permissions"].get("computer_observe") == "deny", agent["name"]

    def test_only_the_ui_checker_uses_the_shared_browser(self):
        # Browser checks moved off coder and tester (their most expensive steps).
        users = {a["name"] for a in db.DEFAULT_AGENTS if "shared browser" in self._caps(a["name"])}
        assert users == {"ui_checker"}

    def test_each_agent_keeps_what_its_job_needs(self):
        assert {"edit files", "run commands"} <= set(self._caps("coder"))
        assert {"edit files", "run commands"} <= set(self._caps("tester"))
        assert {"run commands", "git diff"} <= set(self._caps("the_reviewer"))
        assert "edit files" not in self._caps("the_reviewer")
        assert "web search" in self._caps("researcher")
        assert not {"edit files", "run commands", "web search"} & set(self._caps("architect"))

    def test_working_agents_lack_organiser_admin_and_rollback_tools(self):
        for name in ("architect", "coder", "tester", "the_reviewer", "researcher", "scout"):
            perms = next(a for a in db.DEFAULT_AGENTS if a["name"] == name)["tool_permissions"]
            for tool in ("molt_rollback", "memory_forget", "task_add", "todo_add", "conversation_search", "office_edit"):
                assert perms.get(tool) == "deny", (name, tool)

    def test_researcher_reads_the_web_but_changes_nothing(self):
        caps = self._caps("researcher")
        assert {"web search", "browser", "read files"} <= set(caps)
        assert not {"edit files", "run commands", "git diff"} & set(caps)

    def test_scout_reads_code_and_changes_nothing(self):
        caps = self._caps("scout")
        assert {"read files", "git diff"} <= set(caps)
        assert not {"edit files", "run commands", "web search", "browser", "shared browser"} & set(caps)

    def test_architect_and_coder_are_told_to_use_the_scout(self):
        goals = {a["name"]: a["goal"] for a in db.DEFAULT_AGENTS}
        assert "scout" in goals["architect"]
        assert "request_help" in goals["coder"] and "scout" in goals["coder"]

    def test_scout_and_coder_route_lookups_to_code_search_then_grep(self):
        """The seeded profiles only: this reads ``DEFAULT_AGENTS``, never the live
        database (the user applies the same routing there themselves)."""
        goals = {a["name"]: a["goal"] for a in db.DEFAULT_AGENTS}
        for name in ("scout", "coder"):
            goal = goals[name]
            assert "code_search first for a definition, a symbol, or where something lives" in goal
            assert 'kind="symbol"' in goal  # finding a definition by name
            assert "grep for exact strings" in goal
        # Routing, not a rewrite: the rest of each profile survives.
        assert "Search before you read" in goals["scout"]
        assert "reply as answer" in goals["scout"].lower()
        assert "run the narrowest test that proves the change" in goals["coder"]
        assert "Ask scout with request_help" in goals["coder"]

    def test_architect_sends_code_questions_to_the_scout_even_when_it_could_read(self):
        goal = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")["goal"]
        assert "go to scout, even when you could answer them yourself" in goal


def test_architect_reviews_risky_changes_checks_claims_and_keeps_docs_with_the_change():
    goal = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")["goal"]
    assert "send it to the_reviewer when it touches file paths, auth" in goal
    assert "Passing tests are not a review" in goal
    assert "claim about the environment" in goal and "say it is unverified" in goal
    assert "Doc and plan updates go in the same task as the change" in goal


def test_coder_proves_fixes_keeps_the_loop_free_and_checks_paths_properly():
    goal = next(a for a in db.DEFAULT_AGENTS if a["name"] == "coder")["goal"]
    assert "new test fails without the fix and passes with it" in goal
    assert "never block the event loop" in goal and "asyncio.to_thread" in goal
    assert "is_relative_to(base), never with a string prefix" in goal


def test_the_full_suite_runs_once_per_batch_in_parallel():
    goals = {a["name"]: a["goal"] for a in db.DEFAULT_AGENTS}
    assert "The full suite runs once per batch of related changes" in goals["architect"]
    assert "full test suite when shared code" not in goals["architect"]
    assert "Run the full test suite only when your brief asks for it" in goals["coder"]
    assert "Re-run the coder's targeted tests" in goals["the_reviewer"]
    for name in ("architect", "coder", "tester", "the_reviewer"):
        assert "pytest -n auto" in goals[name]
    for name in ("coder", "tester"):
        assert "run only the test files for what you changed" in goals[name]
        assert "once per batch" in goals[name]


def test_switch_footer_allows_handing_work_to_a_teammate():
    msg = agents.build_agent_switch_msg({"name": "x", "display_name": "X", "goal": "g"})["content"]
    assert "hand it to a teammate" in msg
    assert "do it directly" not in msg


def test_architect_briefs_the_what_and_leaves_the_how_to_the_coder():
    architect = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")
    assert "Brief the what, not the how" in architect["goal"]
    assert "function signatures" not in architect["backstory"]
    assert "don't add a separate test step after the review" in architect["goal"]
    assert "the coder steps depend on the tester step" in architect["goal"]
    assert "have the researcher check them first" in architect["goal"]
    assert "at most about 8 files" in architect["goal"]
    assert "at most 4 related items" in architect["goal"]
    assert "never backend and frontend in the same task" in architect["goal"]
    assert "Report what actually happened, not the plan" in architect["goal"]


def test_architect_checks_a_feature_the_way_the_user_will_use_it():
    # Milestone 7: every check posted straight to the server, none used the page's own form.
    architect = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")
    goal = architect["goal"]
    assert "check it the way the user will use it" in goal
    assert "a page through its UI in a browser (ui_checker" in goal
    ui_checker = next(a for a in db.DEFAULT_AGENTS if a["name"] == "ui_checker")
    assert "web-app-testing" in ui_checker["skills"]  # its fallback when a project has no harness
    assert "scripts/ui_check.py" in ui_checker["goal"]
    assert "At most 8 expectations" in ui_checker["goal"]


def test_architect_briefs_only_seen_facts_and_recommends_what_the_request_promises():
    # Milestone 8: a brief claimed "version 1" (it was 2) and kept "git stash? no -" in its text,
    # and the option listed first broke existing ledger files although the request said
    # "keeping behaviour identical".
    goal = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")["goal"]
    assert "State a fact about the code only if you or a scout saw it" in goal
    assert "no thinking aloud" in goal
    assert 'marked "(recommended)"' in goal
    assert "Recommend the option that keeps what the request already promises" in goal


def test_architect_checks_commands_and_keeps_the_users_choices_in_every_question():
    # 2026-11-23: a brief told the coder to run `golden-init --limit 3`, a flag that doesn't exist.
    # Two questions about scope and budget marked no recommendation (the rule sat in the
    # design-choice bullet), and the second listed "promote the drafts as they are" first although
    # the user had chosen to verify them before any run.
    goal = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")["goal"]
    assert "check that its subcommand and flags exist" in goal
    rule = next(line for line in goal.splitlines() if line.startswith("- Every ask_question"))
    assert 'marked "(recommended)"' in rule
    assert "a scope, a budget" in rule
    assert "no option may quietly drop one" in rule


def test_architect_prompt_matches_the_sub_agent_step_cap():
    architect = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")
    assert f"stops after about {agents.SUB_AGENT_MAX_STEPS} steps" in architect["goal"]


def test_coding_workers_get_a_trimmed_toolset():
    # Measured over the team's runs: coders never called these, and each one's
    # definition was sent on every request.
    for name in ("coder", "tester"):
        perms = next(a for a in db.DEFAULT_AGENTS if a["name"] == name)["tool_permissions"]
        for tool in ("collab_browser_open", "background_wait", "delegate_background", "office_read",
                     "spreadsheet_read", "memory_save", "memory_recall", "tool_search"):
            assert perms.get(tool) == "deny", (name, tool)
        assert perms["read"] == perms["edit"] == perms["bash"] == "auto"


def test_goals_point_at_the_new_tools_and_loop_rules():
    goals = {a["name"]: a["goal"] for a in db.DEFAULT_AGENTS}
    assert "read tool before you edit it" in goals["coder"]
    assert "Run the diagnostics tool on your changed files" in goals["coder"]
    assert "plan_update" in goals["coder"]
    assert "OUTCOME / FILES" in goals["coder"]
    assert 'kind="reference"' in goals["scout"]
    assert "after two fix rounds" in goals["architect"]
    assert "A batch is done when" in goals["architect"]
    assert "not deliberation" in goals["architect"]
    assert "sibling prefix" in goals["the_reviewer"]
    assert "run one probe that would show it false" in goals["the_reviewer"]


def test_architect_compacts_early_and_others_use_the_global_cap():
    caps = {a["name"]: a.get("compaction_max_tokens") for a in db.DEFAULT_AGENTS}
    assert caps["architect"] == 200_000
    assert all(cap is None for name, cap in caps.items() if name != "architect")


def test_defects_the_change_introduced_are_fixed_not_offered():
    # Eval run 1: the reviewer found a raw traceback the new CLI code caused, passed it as
    # "outside the spec", and the architect offered the fix instead of making it.
    goals = {a["name"]: a["goal"] for a in db.DEFAULT_AGENTS}
    assert "A defect the batch itself introduced" in goals["architect"]
    assert "don't offer it to the user" in goals["architect"]
    assert "raw traceback or data loss that the change introduces is such a bug" in goals["the_reviewer"]


def test_test_only_follow_ups_skip_the_second_review():
    # Eval run 2: a re-review after a test-only follow-up cost ~250k tokens and found nothing.
    architect = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")
    assert "the reviewer passed the final code" in architect["goal"]
    assert "only changes tests after a PASS" in architect["goal"]


def test_build_steps_are_part_of_done():
    # Eval run 5: the frontend change was left unbuilt with "say the word and I'll rebuild".
    architect = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")
    assert "every build or copy step the project's rules" in architect["goal"]
    assert "never leave it as an offer" in architect["goal"]


def test_engineer_is_a_solo_fast_path():
    # crabagent_bugfix through the architect: 3-5M tokens and 11-14 min for four small bugs.
    from crabagent.serve.api.prompt import profile_tool_names

    engineer = next(a for a in db.DEFAULT_AGENTS if a["name"] == "engineer")
    tools = set(profile_tool_names(engineer))
    assert {"read", "edit", "write", "bash", "run_tests", "diagnostics", "git_diff", "git_status",
            "code_search", "switch_agent"} <= tools
    # Solo: it can't fan out to the team, and has no browser or office tools.
    assert not tools & {"delegate_task", "delegate_parallel", "run_pipeline", "handoff_to", "plan_task",
                        "delegate_background", "collab_browser_open", "office_read"}
    perms = engineer["tool_permissions"]
    assert perms["edit"] == perms["bash"] == perms["run_tests"] == perms["diagnostics"] == "auto"
    goal = engineer["goal"]
    assert "Scope check first" in goal
    assert 'switch_agent, agent_name "architect"' in goal
    assert "reproduce it first" in goal and "failing without the fix" in goal
    assert "every build or copy step" in goal
    assert "is yours to fix, not to report" in goal


def test_general_hands_any_change_over_at_once_to_the_engineer_by_default():
    # Two evals: with a "contained vs big" judgement General investigated first (21 and 11 commands,
    # approval prompts) and sent four small bug fixes to the architect, with a summary saying "hand to X".
    general = next(a for a in db.DEFAULT_AGENTS if a["name"] == "general")
    first_line = general["goal"].split("\n", 1)[0]
    assert "hand over at once with switch_agent" in first_line
    assert "to the engineer by default" in first_line
    assert "architect only for a project-sized feature or when the user asks for the team" in first_line
    assert "don't weigh its size" in first_line
    assert "without running a command or reading code first" in general["goal"]
    assert 'not "hand this to X"' in general["goal"]
    assert "who is taking over" in general["goal"]


def test_architect_hands_contained_requests_to_the_engineer():
    architect = next(a for a in db.DEFAULT_AGENTS if a["name"] == "architect")
    assert 'switch_agent to "engineer"' in architect["goal"]
    assert "unless the user asked for the team" in architect["goal"]
    assert architect["tool_permissions"].get("switch_agent", "auto") != "deny"


def test_rebuilt_bundles_are_not_scope_creep():
    # Engineer eval: it built the frontend but didn't copy it, calling ~55 renamed bundles "outside this change".
    goals = {a["name"]: a["goal"] for a in db.DEFAULT_AGENTS}
    assert "expected output, not scope creep" in goals["engineer"]
    assert "expected output, not scope creep" in goals["coder"]
