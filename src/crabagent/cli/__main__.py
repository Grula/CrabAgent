from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from prompt_toolkit.styles import Style

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.loop import run_agent
from crabagent.core.config import settings
from crabagent.core.event import AgentEvent, EventType

SLASH_COMMANDS = [
    "/exit",
    "/quit",
    "/help",
    "/clear",
    "/history",
    "/model",
    "/models",
    "/provider",
    "/skills",
    "/skill",
    "/sessions",
    "/session",
    "/new",
    "/molt",
    "/todo",
    "/image",
]
PROMPT_STYLE = Style.from_dict(
    {
        "status": "#888888",
        "toolbar": "bg:#1a1a2e #888888",
    }
)
CLI_USERNAME = "__cli__"


def main():
    parser = argparse.ArgumentParser(
        prog="crabagent",
        description="CrabAgent - AI Agent Platform",
    )
    parser.add_argument("query", nargs="?", help="Query to send to the agent")
    parser.add_argument("--workspace", "-w", type=Path, help="Workspace directory")
    parser.add_argument("--provider", "-p", help="Provider name to use")
    parser.add_argument("--model", "-m", help="Model to use")
    parser.add_argument("--session", "-s", help="Resume a session by session_id")
    parser.add_argument("--max-iterations", type=int, default=50, help="Max iterations")
    parser.add_argument("--serve", action="store_true", help="Start in serve mode")
    parser.add_argument("--host", default=settings.serve_host, help="Serve host")
    parser.add_argument("--port", type=int, default=settings.serve_port, help="Serve port")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show diagnostic logs")
    parser.add_argument("--show-thinking", action="store_true", help="Show streamed model reasoning")
    parser.add_argument("--mcp", action="store_true", help="Enable MCP tools for this one-off request")
    parser.add_argument(
        "--mcp-server",
        action="store_true",
        help="Run a read-only MCP server (stdio) exposing read/glob/grep",
    )
    parser.add_argument("--no-persist", action="store_true", help="Disable conversation persistence")
    parser.add_argument("--old", action="store_true", help="Use legacy single-panel TUI")

    args = parser.parse_args()
    subcmd_words = {"init", "skill", "models"}

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger("LiteLLM").setLevel(logging.WARNING)
    logging.getLogger("primp").setLevel(logging.WARNING)
    logging.getLogger("ddgs.ddgs").setLevel(logging.WARNING)

    from crabagent.core import configure_litellm

    configure_litellm()

    if args.mcp_server:
        # Read-only MCP server on stdio: stdout carries JSON-RPC frames only.
        # Takes precedence over --serve; the workspace defaults to the cwd.
        from crabagent.mcp_server import run as run_mcp_server

        sys.exit(run_mcp_server(args.workspace or Path.cwd()))

    if args.serve:
        try:
            _run_serve(args)
        except KeyboardInterrupt:
            pass
        return

    if args.query and args.query.split()[0].lower() in subcmd_words:
        _dispatch_subcommand(args)
        return

    if not args.query:
        if getattr(args, "old", False):
            from crabagent.cli.tui import run_tui

            asyncio.run(run_tui(args))
        else:
            from crabagent.cli.tui2 import run_dual_tui

            asyncio.run(run_dual_tui(args))
    else:
        sys.exit(asyncio.run(_run_single(args)))


def _dispatch_subcommand(args):
    remaining = args.query.split()
    subcmd = remaining[0].lower()
    if subcmd == "skill":
        _dispatch_skill(remaining[1:])
    elif subcmd == "models":
        asyncio.run(_cmd_models())
    elif subcmd == "init":
        asyncio.run(_cmd_init())


def _dispatch_skill(argv: list[str]):
    parser = argparse.ArgumentParser(prog="crabagent skill")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("list", help="List available skills")
    show_p = sub.add_parser("show", help="Show a skill")
    show_p.add_argument("name")
    args = parser.parse_args(argv)
    asyncio.run(_cmd_skill(args))


async def _cmd_skill(args):
    from crabagent.core.agent.skill.loader import discover_skills

    dirs = settings.skill_discovery_dirs()
    skills = discover_skills(dirs)

    if args.cmd == "list":
        if not skills:
            print("No skills found in:", ", ".join(str(d) for d in dirs if d.exists()))
            return
        for s in sorted(skills.values(), key=lambda x: x.name):
            aux = f" ({len(s.auxiliary_files)} files)" if s.auxiliary_files else ""
            print(f"  {s.name}{aux}")
            print(f"    {s.description}")
            print(f"    location: {s.location}")
    elif args.cmd == "show":
        skill = skills.get(args.name)
        if not skill:
            names = ", ".join(sorted(skills.keys())) if skills else "(none)"
            print(f"Skill '{args.name}' not found. Available: {names}")
            return
        from crabagent.core.agent.skill.loader import format_skill_content

        print(format_skill_content(skill))
    else:
        print("Usage: crabagent skill {list|show}")


async def _cmd_models():
    from crabagent.core.provider_store import get_default_provider

    provider = await get_default_provider()
    if not provider:
        print("No default provider configured.")
        return
    from crabagent.core.provider_store import fetch_models

    try:
        models = await fetch_models(provider.name)
        for m in models:
            print(m)
    except Exception as e:
        print(f"Error fetching models: {e}")


async def _cmd_init():
    from crabagent.core.database import init_db

    await init_db()
    print("CrabAgent initialized.")


async def _ensure_cli_user():
    from sqlalchemy import select

    from crabagent.core.database import User, async_session_factory

    async with async_session_factory() as db:
        result = await db.execute(select(User).where(User.username == CLI_USERNAME))
        user = result.scalar_one_or_none()
        if user:
            return user
        from crabagent.core.auth_utils import hash_password

        user = User(
            username=CLI_USERNAME,
            password_hash=hash_password("cli"),
            role="user",
            enabled=True,
        )
        db.add(user)
        await db.commit()
        return user


async def _init_conversation(user_id: int, workspace: str, model: str, title: str = ""):
    from crabagent.core.database import async_session_factory
    from crabagent.serve.services.conversation import create_conversation

    async with async_session_factory() as db:
        return await create_conversation(db, user_id=user_id, workspace=workspace, model=model, title=title)


async def _load_conversation(session_id: str, user_id: int):
    from crabagent.core.database import async_session_factory
    from crabagent.serve.services.conversation import get_conversation
    from crabagent.serve.services.message import get_messages, message_to_dict

    async with async_session_factory() as db:
        conv = await get_conversation(db, session_id)
        if not conv:
            print(f"Session '{session_id}' not found.")
            return None, [], 0
        if conv.user_id != user_id:
            print(f"Session '{session_id}' not found.")
            return None, [], 0
        msgs = await get_messages(db, conv.id)
        max_seq = max((m.sequence for m in msgs), default=0)
        history = [message_to_dict(m) for m in msgs if m.role != "stats"]
        return conv, history, max_seq


def _make_cli_event_handler(console, show_thinking: bool = False):
    thinking_started = [False]
    text_buffer = []
    live = [None]
    tool_live = [None]
    tool_buffer: dict[str, dict] = {}

    def _ensure_live():
        if console and not live[0]:
            from rich.live import Live
            from rich.markdown import Markdown

            live[0] = Live(
                Markdown(""),
                console=console,
                refresh_per_second=20,
                auto_refresh=False,
                vertical_overflow="visible",
                screen=False,
            )
            live[0].start()

    def _update_live(text: str):
        if not live[0]:
            return
        from rich.markdown import Markdown

        live[0].update(Markdown(text), refresh=True)

    def _stop_live():
        if live[0]:
            live[0].stop()
            live[0] = None

    def _ensure_tool_live():
        if console and not tool_live[0]:
            from rich.live import Live
            from rich.text import Text

            tool_live[0] = Live(
                Text(""),
                console=console,
                refresh_per_second=20,
                auto_refresh=False,
                vertical_overflow="visible",
                screen=False,
            )
            tool_live[0].start()

    def _stop_tool_live():
        if tool_live[0]:
            tool_live[0].stop()
            tool_live[0] = None
            tool_buffer.clear()

    def _render_tools():
        if not tool_live[0]:
            return
        from rich.text import Text

        lines = Text()
        for cid, t in tool_buffer.items():
            name = t["display"]
            source = t.get("source", "builtin")
            prefix = "🔌 " if source == "mcp" else ""
            style = "bright_magenta" if source == "mcp" else "cyan"
            lines.append(f"  {prefix}→ {name}\n", style=style)
            if t["status"] == "done":
                result_text = t["result"]
                if len(result_text) > 300:
                    result_text = result_text[:300] + "..."
                lines.append(f"  ← {result_text}\n", style="dim")
            else:
                lines.append("  …\n", style="dim")
            lines.append("\n")
        tool_live[0].update(lines, refresh=True)

    def _format_tool_display(name: str, args: dict) -> str:
        arg_keys = list(args.keys())
        if name == "read" and "file_path" in args:
            return f"read {args['file_path']}"
        if name in ("write", "edit") and "file_path" in args:
            return f"{name} {args['file_path']}"
        if name == "bash" and "command" in args:
            cmd = str(args["command"])[:80]
            return f"bash {cmd}"
        if name in ("web_search", "web_scrape"):
            q = args.get("query") or args.get("url") or ""
            return f"{name} {str(q)[:60]}"
        if arg_keys:
            first = arg_keys[0]
            val = str(args[first])[:60]
            return f"{name} {first}={val}"
        return name

    def on_event(event: AgentEvent):
        if event.type == EventType.THINKING_DELTA:
            if not show_thinking:
                return
            text = event.data.get("text", "")
            if not thinking_started[0]:
                thinking_started[0] = True
                if console:
                    console.print("Thinking: ", end="", style="dim italic", highlight=False)
                else:
                    print("Thinking: ", end="", flush=True)
            if console:
                console.print(text, end="", style="dim", highlight=False)
            else:
                print(text, end="", flush=True)
        elif event.type == EventType.THINKING_DONE:
            if not show_thinking:
                return
            thinking_started[0] = False
            if console:
                console.print()
            else:
                print()
        elif event.type == EventType.TEXT_DELTA:
            if thinking_started[0]:
                thinking_started[0] = False
                if console:
                    console.print()
                else:
                    print()
            _stop_tool_live()
            text_buffer.append(event.data.get("text", ""))
            if console:
                _ensure_live()
                _update_live("".join(text_buffer))
            else:
                print(event.data.get("text", ""), end="", flush=True)
        elif event.type == EventType.TEXT_DONE:
            # Reasoning-only providers may not emit TEXT_DELTA; use the final event text.
            full = "".join(text_buffer) or event.data.get("text", "")
            text_buffer.clear()
            if not full.strip():
                print()
                return
            if not console:
                print()
                return
            if live[0]:
                _update_live(full)
                _stop_live()
            elif not show_thinking:
                from rich.markdown import Markdown

                console.print(Markdown(full))
        elif event.type == EventType.TOOL_CALL:
            name = event.data.get("name", "")
            call_args = event.data.get("arguments", {})
            call_id = event.data.get("id", "")
            source = event.data.get("source", "builtin")
            server = event.data.get("server_name", "")
            display = _format_tool_display(name, call_args)
            tool_buffer[call_id] = {
                "display": display,
                "status": "pending",
                "result": "",
                "source": source,
                "server": server,
            }
            _ensure_tool_live()
            _render_tools()
        elif event.type == EventType.TOOL_RESULT:
            result = event.data.get("result", "")
            call_id = event.data.get("id", "")
            if call_id in tool_buffer:
                first_line = result.split("\n")[0] if result else ""
                tool_buffer[call_id]["status"] = "done"
                tool_buffer[call_id]["result"] = first_line[:300]
                _render_tools()
            if tool_buffer and all(t["status"] == "done" for t in tool_buffer.values()):
                _stop_tool_live()
        elif event.type == EventType.AGENT_ERROR:
            err = event.data.get("error", "Unknown error")
            if console:
                console.print(f"\n[red]Error: {err}[/red]")
            else:
                print(f"\nError: {err}")
        elif event.type == EventType.BUDGET_EXHAUSTED:
            if console:
                console.print("\n[yellow]Budget exhausted, generating summary...[/yellow]")
            else:
                print("\nBudget exhausted, generating summary...")
        elif event.type == EventType.CONTEXT_COMPRESSED:
            orig = event.data.get("original_count", "?")
            comp = event.data.get("compressed_count", "?")
            if console:
                console.print(f"\n  [dim yellow]Context compressed: {orig} \u2192 {comp} messages[/dim yellow]")
            else:
                print(f"\n  Context compressed: {orig} -> {comp} messages")

    return on_event


def _make_cli_confirm_callback(console):
    async def confirm(tool_name: str, args: dict) -> bool:
        args_str = json.dumps(args, ensure_ascii=False)[:120]
        if console:
            console.print(f"\n[bold yellow]\u26a0 Tool requires permission: {tool_name}[/bold yellow]")
            console.print(f"  [dim]{args_str}[/dim]")
        else:
            print(f"\n\u26a0 Tool requires permission: {tool_name}")
            print(f"  {args_str}")
        try:
            from prompt_toolkit import prompt as pt_prompt

            answer = await asyncio.get_event_loop().run_in_executor(
                None, lambda: pt_prompt("  Allow? [y/N]: ").strip().lower()
            )
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in ("y", "yes")

    return confirm


def _make_cli_ask_callback(console):
    async def ask(question: str, options: list[str] | None = None, multiple: bool = False) -> str:
        try:
            if options:
                print(f"\n  {question}")
                for i, opt in enumerate(options, 1):
                    print(f"    {i}. {opt}")
                if multiple:
                    print(
                        f"  Choice (1-{len(options)}, comma-separated for multiple, or custom): ",
                        end="", flush=True,
                    )
                    answer = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input().strip()
                    )
                    parts = [p.strip() for p in answer.split(",") if p.strip()]
                    mapped = []
                    for p in parts:
                        try:
                            idx = int(p) - 1
                            mapped.append(options[idx] if 0 <= idx < len(options) else p)
                        except ValueError:
                            mapped.append(p)
                    return ", ".join(mapped)
                print(f"  Choice (1-{len(options)} or custom): ", end="", flush=True)
                answer = await asyncio.get_event_loop().run_in_executor(None, lambda: input().strip())
                try:
                    idx = int(answer) - 1
                    if 0 <= idx < len(options):
                        return options[idx]
                except ValueError:
                    pass
                return answer
            else:
                from prompt_toolkit import prompt as pt_prompt

                answer = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: pt_prompt(f"  {question}: ").strip()
                )
                return answer
        except (EOFError, KeyboardInterrupt):
            return ""

    return ask


async def _setup_agent_context(
    args,
    conversation_id: int | None = None,
    history: list[dict] | None = None,
    persistence_start_seq: int = 0,
    session_id_str: str | None = None,
    user_id: int = 0,
):
    import crabagent.core.agent.tools.bash  # noqa: F401
    import crabagent.core.agent.tools.edit  # noqa: F401
    import crabagent.core.agent.tools.glob  # noqa: F401
    import crabagent.core.agent.tools.grep  # noqa: F401
    import crabagent.core.agent.tools.read  # noqa: F401
    import crabagent.core.agent.tools.web  # noqa: F401
    import crabagent.core.agent.tools.write  # noqa: F401
    import crabagent.core.agent.tools.office  # noqa: F401

    try:
        import crabagent.core.agent.tools.browser  # noqa: F401
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
    from crabagent.core.agent.tools import registry as _registry

    # Detect OfficeCLI for office document tools
    from crabagent.core.office.manager import get_office_manager

    office_mgr = get_office_manager()
    await office_mgr.detect()

    workspace = args.workspace or settings.workspace
    workspace = workspace.resolve()

    from crabagent.core.agent.system_prompt import build_system_prompt

    base_prompt = await build_system_prompt(workspace, user_id=user_id, interface="terminal")

    context = AgentContext(
        workspace=workspace,
        tool_registry=_registry,
        max_iterations=args.max_iterations,
        model=getattr(args, "model", None),
        provider_name=args.provider,
        system_prompt=base_prompt,
    )

    if history:
        context.messages = list(history)

    if user_id:
        context.metadata["user_id"] = user_id
        from crabagent.core.conversations.history import history_access_enabled

        context.metadata["conversation_history_tool_enabled"] = await history_access_enabled(user_id)
        if not context.metadata["conversation_history_tool_enabled"]:
            context.tool_registry.unregister("conversation_search")
    context.metadata["workspace_path"] = str(workspace)

    if conversation_id and session_id_str:
        context.metadata["session_id"] = session_id_str
        context.metadata["branch_id"] = "main"

    from crabagent.core.agent.skill.loader import discover_skills, register_skill_tool

    skill_dirs = settings.skill_discovery_dirs()
    skills = discover_skills(skill_dirs)
    if skills:
        register_skill_tool(context.tool_registry, skills)
    context.metadata["_skills"] = skills

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

    # MCP stdio servers can take several seconds to boot. Keep one-off requests
    # fast by loading them only when the caller explicitly asks for MCP tools.
    if getattr(args, "mcp", False):
        from crabagent.core.mcp.client import MCPClientManager
        from crabagent.core.mcp.tools import register_mcp_tools

        mcp_manager = MCPClientManager(quiet=not getattr(args, "verbose", False))
        await mcp_manager.start_all()
        register_mcp_tools(context.tool_registry, mcp_manager)
        context.metadata["_mcp_manager"] = mcp_manager

    if conversation_id and not getattr(args, "no_persist", False):
        from crabagent.serve.services.persistence import PersistenceListener

        persistence = PersistenceListener(conversation_id=conversation_id)
        if history:
            persistence.sequence = persistence_start_seq if persistence_start_seq > 0 else len(history)
        context.event_bus.subscribe(persistence.on_event)

    try:
        from rich.console import Console

        console = Console()
    except ImportError:
        console = None

    context.event_bus.subscribe(
        _make_cli_event_handler(console, show_thinking=getattr(args, "show_thinking", False))
    )

    if not settings.auto_approve_tools:
        context.confirm_callback = _make_cli_confirm_callback(console)

    context.ask_callback = _make_cli_ask_callback(console)

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

    return context


async def _run_single(args):
    from crabagent.core.database import init_db

    await init_db()

    user = await _ensure_cli_user()

    ok = await _ensure_provider_configured()
    if not ok:
        sys.exit(1)

    conversation_id = None
    session_id_str = None

    if not getattr(args, "no_persist", False):
        if getattr(args, "session", None):
            conv, history, max_seq = await _load_conversation(args.session, user.id)
            if conv is None:
                sys.exit(1)
            conversation_id = conv.id
            session_id_str = conv.session_id
            if conv.workspace:
                workspace = Path(conv.workspace).resolve()
            else:
                workspace = (args.workspace or settings.workspace).resolve()
            args.workspace = workspace
        else:
            workspace = args.workspace or settings.workspace
            workspace = workspace.resolve()
            conv = await _init_conversation(
                user.id,
                workspace=str(workspace),
                model=args.model or "",
                title=args.query[:50] if len(args.query) > 50 else args.query,
            )
            conversation_id = conv.id
            session_id_str = conv.session_id
            history = None
            max_seq = 0
    else:
        history = None
        max_seq = 0

    context = await _setup_agent_context(
        args,
        conversation_id=conversation_id,
        history=history,
        persistence_start_seq=max_seq,
        session_id_str=session_id_str,
        user_id=user.id,
    )

    try:
        await run_agent(context, args.query)
    except KeyboardInterrupt:
        pass
    finally:
        mcp_mgr = context.metadata.get("_mcp_manager")
        if mcp_mgr:
            try:
                await mcp_mgr.stop_all()
            except Exception:
                pass
        browser_mgr = context.metadata.get("_browser_manager")
        if browser_mgr:
            try:
                await browser_mgr.close()
            except Exception:
                pass

    if session_id_str:
        from crabagent.core.database import async_session_factory
        from crabagent.serve.services.conversation import update_conversation

        async with async_session_factory() as db:
            await update_conversation(db, session_id_str)

    # A setup failure is reported as AGENT_ERROR and returns normally; a failed
    # run must not look like a successful one to a script.
    return 1 if context.metadata.get("_run_error") else 0


async def _ensure_provider_configured():
    from crabagent.core.provider_store import (
        PROVIDER_CATALOG,
        create_provider,
        list_providers,
    )

    providers = await list_providers()
    if providers:
        return True

    print("\nNo LLM provider configured. CrabAgent needs at least one provider to function.\n")
    print("Available provider types:")
    for key, info in PROVIDER_CATALOG.items():
        print(f"  {key}: {info['display_name']}")
    print()

    while True:
        try:
            choice = input("Would you like to add a provider now? [Y/n]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCannot continue without a provider.")
            return False

        if choice in ("n", "no"):
            print("Cannot continue without a provider.")
            return False

        ptype = input("Provider type [deepseek]: ").strip().lower() or "deepseek"
        catalog = PROVIDER_CATALOG.get(ptype)
        if not catalog:
            print(f"Unknown provider type '{ptype}'. Available: {', '.join(PROVIDER_CATALOG.keys())}")
            continue

        name = input(f"Name [{ptype}]: ").strip() or ptype
        api_key = input("API key: ").strip()
        if not api_key:
            print("API key is required. Please try again.")
            continue

        try:
            await create_provider(
                name=name,
                display_name=catalog["display_name"],
                provider_type=ptype,
                api_key=api_key,
                base_url=catalog["base_url"],
                is_default=True,
            )
            print(f"\nProvider '{name}' ({catalog['display_name']}) configured successfully!\n")
            return True
        except Exception as e:
            print(f"Error: {e}. Please try again.\n")


def _run_serve(args):
    try:
        import uvicorn
    except ImportError:
        print("Error: 'serve' mode dependencies not found.")
        print("Install with: pip install crabagent")
        sys.exit(1)

    from crabagent.serve.app import create_app

    from crabagent.serve.shutdown import begin_shutdown

    class _Server(uvicorn.Server):
        def handle_exit(self, sig, frame) -> None:
            # Tell event streams to end now; uvicorn then waits for connections to close.
            begin_shutdown()
            super().handle_exit(sig, frame)

    # The collaboration browser bridge never lets the agent open CrabAgent's own
    # API port; it reads the port from here.
    os.environ["CRAB_APP_PORT"] = str(args.port)
    app = create_app()
    # Streams end within a second of Ctrl+C (see serve/shutdown.py). The timeout is
    # a backstop for any other request that would otherwise keep shutdown waiting.
    config = uvicorn.Config(app, host=args.host, port=args.port, timeout_graceful_shutdown=5)
    _Server(config).run()


if __name__ == "__main__":
    main()
