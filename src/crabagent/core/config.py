from pathlib import Path

from pydantic_settings import BaseSettings


def _default_db_url() -> str:
    d = Path.home() / ".crabagent"
    d.mkdir(exist_ok=True)
    return f"sqlite+aiosqlite:///{d / 'crabagent.db'}"


class Settings(BaseSettings):
    model_config = {
        "env_prefix": "CRAB_",
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    workspace: Path = Path.cwd()

    max_iterations: int = 200
    max_tokens: int = 16384
    reasoning_effort: str = "medium"

    skills_dir: Path | None = None
    skills_paths: list[str] = []
    disable_opencode_skills: bool = False

    db_url: str = ""
    jwt_secret: str = "crabagent-secret-change-me"
    jwt_expire_minutes: int = 43_200

    serve_host: str = "0.0.0.0"
    serve_port: int = 5210

    web_proxy: str = ""

    default_model: str = "gpt-4o"

    encryption_key: str = ""

    auto_approve_tools: bool = False

    bash_block_privilege_escalation: bool = True
    bash_max_output_length: int = 50000
    bash_block_background: bool = False

    # Load rarely used tool groups (office, browser, email, ...) only when a
    # conversation needs them, instead of sending every tool definition with
    # every request. Set CRAB_DEFER_TOOLS=false to always send all tools.
    defer_tools: bool = True

    model_token_limits: dict[str, int] = {}
    context_compression_threshold: float = 0.8
    # Also compact once the context passes this many tokens, however large the
    # model's window. 1M = DeepSeek's window, so for it the threshold decides
    # (80% → compaction at 800k). Lower it to compact earlier and re-send less
    # context per step (every step re-sends the whole context). 0 turns the cap off.
    context_compression_max_tokens: int = 1_000_000
    context_keep_recent: int = 6

    memory_auto_extract: bool = True
    # Saved memories (team notes, user preferences) in the main system prompt,
    # plus the automatic preference extraction that feeds them. Off by default:
    # project knowledge lives in each project's AGENTS.md instead. Agents can
    # still save and recall memories with the memory tools either way.
    memory_prompt_enabled: bool = False
    memory_auto_recall: bool = True
    memory_max_inject: int = 5

    # ── LLM retry / resilience ──
    llm_retry_max: int = 3
    llm_retry_base_delay: float = 2.0
    llm_retry_max_delay: float = 60.0
    llm_request_timeout: int = 120

    browser_screenshot_to_llm: bool = True
    browser_screenshot_max_bytes: int = 1_500_000

    molt_keep_count: int = 20

    def model_post_init(self, __context) -> None:
        if not self.db_url:
            self.db_url = _default_db_url()

    @staticmethod
    def _user_config_dir() -> Path:
        d = Path.home() / ".crabagent"
        d.mkdir(exist_ok=True)
        return d

    def get_encryption_key(self) -> str:
        if self.encryption_key:
            return self.encryption_key
        key_path = self._user_config_dir() / "encryption_key"
        if key_path.exists():
            return key_path.read_text().strip()
        from cryptography.fernet import Fernet

        key = Fernet.generate_key().decode()
        key_path.write_text(key)
        return key

    def save_last_model(self, model: str) -> None:
        (self._user_config_dir() / "last_model").write_text(model)

    def load_last_model(self) -> str | None:
        p = self._user_config_dir() / "last_model"
        if p.exists():
            model = p.read_text().strip()
            return model or None
        return None

    def skill_discovery_dirs(self, workspace: Path | None = None) -> list[Path]:
        """Directories scanned for skills, lowest priority first.

        A skill found in a later directory replaces one with the same name from
        an earlier directory, so workspace and user skills override bundled ones.

        ``workspace`` is the project a chat works in. Project skills come from
        that folder, never from the folder the server happened to start in, so
        one project's skills don't leak into another's chats.
        """
        workspace = workspace or self.workspace
        dirs: list[Path] = []
        # Skills shipped with CrabAgent. Listed first so that every workspace
        # gets them, including workspaces created before a skill was added.
        bundled = Path(__file__).resolve().parents[1] / "skills"
        if bundled.is_dir():
            dirs.append(bundled)
        if self.skills_dir:
            dirs.append(self.skills_dir)
        for p in self.skills_paths:
            dirs.append(Path(p))
        candidate = workspace / ".crabagent" / "skills"
        if candidate.exists():
            dirs.append(candidate)
        if not self.disable_opencode_skills:
            candidate = workspace / ".opencode" / "skills"
            if candidate.exists():
                dirs.append(candidate)
        home = Path.home()
        global_dir = home / ".crabagent" / "skills"
        if global_dir.exists():
            dirs.append(global_dir)
        if not self.disable_opencode_skills:
            global_opencode = home / ".opencode" / "skills"
            if global_opencode.exists():
                dirs.append(global_opencode)
        return dirs


settings = Settings()
