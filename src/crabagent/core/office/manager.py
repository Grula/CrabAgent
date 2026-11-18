from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"


@dataclass
class OfficeResult:
    """Wraps the result of an OfficeCLI command execution."""

    success: bool
    data: Any = None
    error: str = ""
    raw_output: str = ""


# ── known locations to probe when officecli is not on PATH ──────────────
if IS_WINDOWS:
    _PROBE_LOCATIONS = [
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "officecli", "officecli.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "officecli", "officecli.exe"),
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "officecli", "officecli.exe"),
        os.path.expanduser("~/.officecli/officecli.exe"),
    ]
else:
    _PROBE_LOCATIONS = [
        "/usr/local/bin/officecli",
        "/opt/homebrew/bin/officecli",
        os.path.expanduser("~/.officecli/officecli"),
        os.path.expanduser("~/.local/bin/officecli"),
        "/usr/bin/officecli",
    ]

# ── install hint (platform-specific) ────────────────────────────────────
if IS_WINDOWS:
    _INSTALL_HINT = (
        "❌ OfficeCLI is not installed.\n\n"
        "Install via winget (recommended):\n"
        "```\n"
        "winget install HaiYing.OfficeCLI\n"
        "```\n"
        "Or download from: https://github.com/iOfficeAI/OfficeCLI/releases\n"
        "(Choose officecli-win-x64.exe for 64-bit Windows)"
    )
else:
    _INSTALL_HINT = (
        "❌ OfficeCLI is not installed.\n\n"
        "Install with one command:\n"
        "```bash\n"
        "curl -fsSL https://raw.githubusercontent.com/iOfficeAI/OfficeCLI/main/install.sh | bash\n"
        "```\n"
        "Or download from: https://github.com/iOfficeAI/OfficeCLI/releases"
    )


# ── auto-install helpers ────────────────────────────────────────────────


def _determine_asset() -> str | None:
    """Determine the OfficeCLI release asset name for the current platform."""
    import platform as _platform

    machine = _platform.machine().lower()
    if machine in ("amd64", "x86_64", "x64"):
        norm = "x64"
    elif machine in ("arm64", "aarch64"):
        norm = "arm64"
    else:
        return None

    if sys.platform == "darwin":
        return f"officecli-mac-{norm}"
    elif sys.platform == "win32":
        return f"officecli-win-{norm}.exe"
    elif sys.platform == "linux":
        is_musl = _detect_musl()
        prefix = "linux-alpine" if is_musl else "linux"
        return f"officecli-{prefix}-{norm}"

    return None


def _detect_musl() -> bool:
    """Detect if running on musl-based Linux (Alpine, etc.)."""
    try:
        import subprocess
        result = subprocess.run(
            ["ldd", "--version"], capture_output=True, text=True, timeout=10
        )
        if "musl" in (result.stdout + result.stderr).lower():
            return True
    except Exception:
        pass
    try:
        with open("/etc/alpine-release"):
            return True
    except Exception:
        pass
    return False


async def _resolve_latest_version() -> str | None:
    """Resolve the latest OfficeCLI release version tag via redirect.

    Follows /releases/latest and extracts the tag from the final URL.
    Tries CDN mirror first, then GitHub.
    """
    import httpx

    urls = [
        "https://d.officecli.ai/releases/latest",
        "https://github.com/iOfficeAI/OfficeCLI/releases/latest",
    ]
    async with httpx.AsyncClient(timeout=30) as client:
        for url in urls:
            try:
                resp = await client.get(url, follow_redirects=True, timeout=15)
                final_url = str(resp.url)
                if "/tag/v" in final_url:
                    version = final_url.rsplit("/tag/", 1)[-1]
                    if version.startswith("v"):
                        logger.debug("Resolved latest OfficeCLI version: %s", version)
                        return version
            except Exception:
                continue
    return None


class OfficeManager:
    """OfficeCLI binary manager.

    Responsibilities:
    1. Detect whether OfficeCLI is installed on the system
    2. Expose a type-safe command execution API (async subprocess)
    3. Parse JSON output
    4. Manage the long-lived resident-mode process (reserved for future
       batch-operation optimizations)
    """

    BINARY_NAME = "officecli"

    def __init__(self) -> None:
        self._binary_path: str | None = None
        self._available: bool = False
        self._version: str = ""
        self._install_status: dict = {"status": "not_found", "message": "", "progress": 0}
        self._install_task: asyncio.Task | None = None
        self._perf_stats: dict[str, list[float]] = {}

    def get_install_status(self) -> dict:
        """Return current install progress (thread-safe read)."""
        return dict(self._install_status)

    def get_perf_stats(self) -> dict[str, dict[str, float | int]]:
        """Return lightweight rolling performance stats for OfficeCLI commands."""
        summary: dict[str, dict[str, float | int]] = {}
        for name, samples in self._perf_stats.items():
            if not samples:
                continue
            summary[name] = {
                "count": len(samples),
                "min_ms": round(min(samples), 2),
                "max_ms": round(max(samples), 2),
                "avg_ms": round(sum(samples) / len(samples), 2),
            }
        return summary

    def _record_perf(self, name: str, elapsed_ms: float) -> None:
        samples = self._perf_stats.setdefault(name, [])
        samples.append(elapsed_ms)
        if len(samples) > 50:
            del samples[:-50]

    # ── detection ────────────────────────────────────────────────────────

    async def detect(self) -> bool:
        """Locate the OfficeCLI binary on this system.

        Checks, in order:
        1. ``officecli`` on PATH
        2. well-known install locations (_PROBE_LOCATIONS)

        Returns True when a usable binary was found.
        """
        # 1) PATH lookup
        path_in_path = shutil.which(self.BINARY_NAME)
        if path_in_path:
            return await self._set_binary(path_in_path)

        # 2) probe known locations
        for candidate in _PROBE_LOCATIONS:
            if os.path.isfile(candidate):
                # On Unix, check executable permission; on Windows,
                # any .exe file that exists is potentially runnable.
                if IS_WINDOWS or os.access(candidate, os.X_OK):
                    return await self._set_binary(candidate)

        logger.info("OfficeCLI not found — office tools will be unavailable")
        return False

    async def _set_binary(self, path: str) -> bool:
        self._binary_path = path
        self._available = True
        try:
            result = await self._run_cmd([path, "--version"])
            self._version = result.strip()
        except Exception:
            self._version = "unknown"
        logger.info("OfficeCLI detected: %s (%s)", path, self._version)
        return True

    # ── auto-install ──────────────────────────────────────────────────────

    async def install(self, force: bool = False) -> bool:
        """Auto-download and install OfficeCLI binary.

        Downloads the appropriate binary for the current platform
        (from CDN mirror with GitHub fallback), verifies the SHA256
        checksum, and installs to ``~/.officecli/``.

        Args:
            force: If True, re-download even if already available.

        Returns:
            True if installation succeeded and the binary is usable.
        """
        if not force and self._available:
            self._install_status.update({"status": "ready", "message": "Already installed", "progress": 100})
            return True

        self._install_status.update({"status": "installing", "message": "Preparing...", "progress": 0})
        import httpx

        dest_dir = Path.home() / ".officecli"
        dest_dir.mkdir(parents=True, exist_ok=True)
        binary_name = "officecli.exe" if IS_WINDOWS else "officecli"
        dest_path = dest_dir / binary_name

        # 1. Determine asset name
        asset = _determine_asset()
        if not asset:
            logger.error("Unsupported platform: %s %s",
                         sys.platform, __import__("platform").machine())
            self._install_status.update({"status": "failed", "message": f"Unsupported platform: {sys.platform}", "progress": 0})
            return False

        # 2. Resolve latest version
        self._install_status.update({"message": "Resolving latest version...", "progress": 5})
        version = await _resolve_latest_version()
        if version:
            logger.info("Latest OfficeCLI version: %s", version)
            mirror_base = f"https://d.officecli.ai/releases/download/{version}"
            github_base = f"https://github.com/iOfficeAI/OfficeCLI/releases/download/{version}"
        else:
            logger.warning("Could not resolve latest version; using 'latest' path")
            mirror_base = "https://d.officecli.ai/releases/latest/download"
            github_base = "https://github.com/iOfficeAI/OfficeCLI/releases/latest/download"

        self._install_status.update({"message": f"Downloading OfficeCLI ({asset})...", "progress": 10})
        logger.info("Downloading OfficeCLI (%s)...", asset)

        # 3. Download binary (mirror → GitHub fallback)
        content: bytes | None = None
        checksum_text: str | None = None

        async with httpx.AsyncClient(timeout=300, follow_redirects=True) as client:
            for idx, url in enumerate((f"{mirror_base}/{asset}", f"{github_base}/{asset}")):
                self._install_status.update({"message": f"Downloading... ({idx + 1}/2)", "progress": 15})
                try:
                    resp = await client.get(url, timeout=120)
                    resp.raise_for_status()
                    content = resp.content
                    logger.info("Downloaded from %s", url)
                    self._install_status.update({"message": "Download complete, verifying...", "progress": 70})
                    break
                except Exception as e:
                    logger.debug("Download failed from %s: %s", url, e)

            if content is None:
                logger.error("Failed to download OfficeCLI from all sources")
                self._install_status.update({"status": "failed", "message": "Download failed from all sources", "progress": 0})
                return False

            # 4. Download SHA256 checksums
            self._install_status.update({"message": "Downloading checksum...", "progress": 75})
            for url in (f"{mirror_base}/SHA256SUMS", f"{github_base}/SHA256SUMS"):
                try:
                    resp = await client.get(url, timeout=30)
                    resp.raise_for_status()
                    checksum_text = resp.text
                    break
                except Exception:
                    continue

        # 5. Verify checksum
        self._install_status.update({"message": "Verifying checksum...", "progress": 80})
        if checksum_text:
            expected_hash = None
            for line in checksum_text.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2 and parts[1] == asset:
                    expected_hash = parts[0]
                    break
            if expected_hash:
                actual_hash = hashlib.sha256(content).hexdigest()
                if actual_hash.lower() != expected_hash.lower():
                    logger.error("Checksum mismatch for %s: expected %s, got %s",
                                 asset, expected_hash, actual_hash)
                    self._install_status.update({"status": "failed", "message": "Checksum mismatch", "progress": 0})
                    return False
                logger.info("Checksum verified")
            else:
                logger.warning("No checksum entry for %s in SHA256SUMS", asset)
        else:
            logger.warning("SHA256SUMS unavailable; skipping checksum verification")

        # 6. Write to temporary path
        self._install_status.update({"message": "Installing...", "progress": 85})
        tmp_path = dest_dir / f"{binary_name}.new"
        try:
            tmp_path.write_bytes(content)
        except OSError as e:
            logger.error("Failed to write binary: %s", e)
            self._install_status.update({"status": "failed", "message": f"Cannot write: {e}", "progress": 0})
            return False

        if not IS_WINDOWS:
            tmp_path.chmod(0o755)

        # 7. macOS: quarantine + ad-hoc code signing
        if sys.platform == "darwin":
            self._install_status.update({"message": "Signing binary...", "progress": 90})
            import subprocess
            try:
                subprocess.run(["xattr", "-d", "com.apple.quarantine", str(tmp_path)],
                               capture_output=True, timeout=30)
                rc = subprocess.run(["codesign", "-v", "--strict", str(tmp_path)],
                                    capture_output=True, timeout=30)
                if rc.returncode != 0:
                    subprocess.run(["codesign", "-s", "-", "-f", str(tmp_path)],
                                   capture_output=True, timeout=30)
            except Exception as e:
                logger.warning("macOS codesign step failed (non-fatal): %s", e)

        # 8. Atomic replace
        self._install_status.update({"message": "Finalizing...", "progress": 95})
        try:
            tmp_path.rename(dest_path)
        except OSError as e:
            logger.error("Failed to install binary: %s", e)
            self._install_status.update({"status": "failed", "message": f"Cannot install: {e}", "progress": 0})
            return False

        # 9. Re-detect
        if await self._set_binary(str(dest_path)):
            logger.info("OfficeCLI installed at %s", dest_path)
            self._install_status.update({"status": "ready", "message": f"Installed v{self._version}", "progress": 100})
            return True
        logger.error("Installed binary at %s failed verification", dest_path)
        self._install_status.update({"status": "failed", "message": "Binary failed verification", "progress": 0})
        return False

    # ── properties ──────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._available

    @property
    def version(self) -> str:
        return self._version

    @property
    def binary_path(self) -> str | None:
        return self._binary_path

    # ── generic command execution ───────────────────────────────────────

    async def exec(self, *args: str, timeout: int = 60) -> OfficeResult:
        """Run an arbitrary OfficeCLI command.

        Parameters
        ----------
        *args : str
            The command and its arguments, e.g. ``"view", "file.pptx", "--mode", "html"``
        timeout : int
            Timeout in seconds (default 60)

        Returns
        -------
        OfficeResult
            If the binary emits JSON (starts with ``{``), it is parsed automatically.
        """
        cmd = [self._binary_path, *args] if self._available else []
        if not cmd:
            return OfficeResult(
                success=False,
                error=_INSTALL_HINT,
            )
        return await self._exec_with_stdin(cmd, timeout=timeout)

    # ── high-level helpers ──────────────────────────────────────────────

    async def view_html(self, file_path: str) -> OfficeResult:
        """Render the document as an HTML preview."""
        return await self.exec("view", file_path, "html")

    async def view_text(
        self,
        file_path: str,
        max_lines: int = 200,
        sheet: str = "",
        cols: str = "",
        start: int = 0,
    ) -> OfficeResult:
        """Read the plain-text content of a document.

        Note: officecli's ``view text`` has no ``--sheet`` option (it fails
        with "unknown option"). xlsx output already contains ``=== Sheet: X ===``
        sections, so sheet filtering is left to the caller (the office_read
        tool layer) and ``sheet`` is ignored here.
        """
        del sheet  # not supported by the CLI, see docstring
        args = ["view", file_path, "text", "--max-lines", str(max_lines)]
        if start > 0:
            args.extend(["--start", str(start)])
        if cols:
            args.extend(["--cols", cols])
        return await self.exec(*args)

    async def view_outline(self, file_path: str) -> OfficeResult:
        """Show the document outline structure."""
        return await self.exec("view", file_path, "outline")

    async def view_stats(self, file_path: str) -> OfficeResult:
        """Show document statistics."""
        return await self.exec("view", file_path, "stats")

    async def create(self, file_path: str) -> OfficeResult:
        """Create a blank document (the type is inferred from the file extension)."""
        return await self.exec("create", file_path)

    async def set_props(
        self, file_path: str, element_path: str, props: dict[str, Any]
    ) -> OfficeResult:
        """Set properties on one element of a document.

        Parameters
        ----------
        element_path : str
            Element path, e.g. ``/slide[1]``, ``/slide[1]/shape[1]``,
            ``/sheet[1]/cell[A1]``
        props : dict
            Key/value pairs to set (text, font, size, color, ...)
        """
        cmd = ["set", file_path, element_path]
        for k, v in props.items():
            cmd.extend(["--prop", f"{k}={v}"])
        return await self.exec(*cmd)

    async def add_element(
        self,
        file_path: str,
        parent_path: str,
        element_type: str,
        props: dict[str, Any] | None = None,
    ) -> OfficeResult:
        """Add a child element to a document.

        Parameters
        ----------
        parent_path : str
            Parent element path, e.g. ``/`` (root), ``/slide[1]``, ``/body``
        element_type : str
            Element type, e.g. ``slide``, ``shape``, ``table``, ``paragraph``,
            ``chart``, ``image``, ``sheet``, ``row``, ``cell``
        props : dict | None
            Element properties. Recognized positioning keys (never forwarded
            to the CLI as --prop):
            - ``index``: insertion index (0-based)
            - ``after``: insert after the element at this path
            - ``before``: insert before the element at this path
            - ``data``: table only, a 2-D array as JSON (passed via stdin)
        """
        cmd = [self._binary_path, "add", file_path, parent_path, "--type", element_type]

        # Separate positioning arguments from ordinary properties
        pass_props: dict[str, Any] = {}
        for k, v in (props or {}).items():
            if k == "index":
                cmd.extend(["--index", str(int(v))])
            elif k in ("after", "before"):
                cmd.extend([f"--{k}", str(v)])
            elif k == "data":
                # data is converted to a CSV string and passed via --prop
                if isinstance(v, list):
                    v = ";".join(",".join(str(c) for c in row) for row in v)
                pass_props[k] = v
            else:
                pass_props[k] = v

        for k, v in pass_props.items():
            cmd.extend(["--prop", f"{k}={v}"])

        # Run through exec (every argument is already in cmd, no stdin needed)
        return await self.exec(*cmd[1:])

    async def remove_element(self, file_path: str, element_path: str) -> OfficeResult:
        """Remove an element from a document."""
        return await self.exec("remove", file_path, element_path)

    async def move_element(
        self,
        file_path: str,
        element_path: str,
        to_parent: str = "",
        index: int = -1,
        after: str = "",
        before: str = "",
    ) -> OfficeResult:
        """Move an element to a new parent or to a given position.

        At least one of to_parent / after / before must be given; index is a
        0-based insertion position.
        """
        cmd = ["move", file_path, element_path]
        if to_parent:
            cmd.extend(["--to", to_parent])
        if after:
            cmd.extend(["--after", after])
        if before:
            cmd.extend(["--before", before])
        if index >= 0:
            cmd.extend(["--index", str(index)])
        return await self.exec(*cmd)

    async def get_element(
        self, file_path: str, element_path: str, depth: int = 1
    ) -> OfficeResult:
        """Get structured JSON information for an element."""
        cmd = ["get", file_path, element_path, "--json"]
        if depth > 1:
            cmd.extend(["--depth", str(depth)])
        return await self.exec(*cmd)

    async def query(
        self, file_path: str, selector: str, max_results: int = 50
    ) -> OfficeResult:
        """Query document elements by selector.

        Parameters
        ----------
        selector : str
            Selector expression, e.g. ``"shape:contains(TODO)"``
        """
        cmd = ["query", file_path, selector, "--json"]
        return await self.exec(*cmd)

    @staticmethod
    def _normalize_batch_props(props: dict[str, Any]) -> dict[str, Any]:
        """Normalize batch props so every value is a CLI-acceptable scalar.

        The CLI rejects arrays as batch prop values (it reports
        ``Unexpected token StartArray for prop value 'data'``), so, as in
        add_element, a 2-D ``data`` array is converted to a CSV string
        (commas between columns, semicolons between rows).
        """
        normalized = dict(props)
        for k, v in props.items():
            if isinstance(v, list):
                if v and isinstance(v[0], list):
                    normalized[k] = ";".join(
                        ",".join(str(c) for c in row) for row in v
                    )
                else:
                    normalized[k] = ",".join(str(c) for c in v)
            elif isinstance(v, (dict, bool)):
                normalized[k] = json.dumps(v, ensure_ascii=False)
        return normalized

    async def batch(
        self, file_path: str, commands: list[dict[str, Any]]
    ) -> OfficeResult:
        """Run multiple operations within a single open/save cycle."""
        safe_commands: list[dict[str, Any]] = []
        for item in commands:
            if not isinstance(item, dict):
                safe_commands.append(item)
                continue
            entry = dict(item)
            props = entry.get("props")
            if isinstance(props, dict):
                props = dict(props)
                # For add, the element type comes from the command level; a
                # stray "type" in props makes the CLI fail without details,
                # so strip it here.
                if entry.get("command") == "add" and "type" in props:
                    props.pop("type")
                entry["props"] = self._normalize_batch_props(props)
            safe_commands.append(entry)
        payload = json.dumps(safe_commands)
        return await self.exec("batch", file_path, "--commands", payload)

    async def help_for(self, fmt: str) -> OfficeResult:
        """Get the help reference for a format (property lists, etc.).

        officecli takes the format as a positional argument --
        ``officecli help <format>`` -- and has no ``--format`` flag.
        """
        return await self.exec("help", fmt)

    # ── internal ────────────────────────────────────────────────────────

    async def _exec_with_stdin(
        self, cmd: list[str], stdin_data: bytes | None = None, timeout: int = 60
    ) -> OfficeResult:
        """Execute a command, optionally passing data via stdin."""
        if not self._available:
            return OfficeResult(
                success=False,
                error=_INSTALL_HINT,
            )

        logger.debug("officecli: %s", " ".join(cmd))
        op_name = cmd[1] if len(cmd) > 1 else "unknown"
        start = time.perf_counter()

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE if stdin_data else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=stdin_data), timeout=timeout
            )
            stdout_decoded = stdout.decode("utf-8", errors="replace")
            stderr_decoded = stderr.decode("utf-8", errors="replace")

            elapsed_ms = (time.perf_counter() - start) * 1000
            self._record_perf(op_name, elapsed_ms)

            if proc.returncode != 0:
                return OfficeResult(
                    success=False,
                    error=stderr_decoded or f"Exit code {proc.returncode}",
                    raw_output=stdout_decoded,
                )

            # Try to parse the output as JSON
            trimmed = stdout_decoded.strip()
            if trimmed.startswith("{"):
                try:
                    payload = json.loads(trimmed)
                    return OfficeResult(
                        success=payload.get("success", True),
                        data=payload.get("data", payload),
                        error=payload.get("error", ""),
                        raw_output=stdout_decoded,
                    )
                except json.JSONDecodeError:
                    pass

            return OfficeResult(success=True, data=stdout_decoded, raw_output=stdout_decoded)

        except TimeoutError:
            return OfficeResult(success=False, error=f"Command timed out ({timeout}s)")
        except FileNotFoundError:
            self._available = False
            return OfficeResult(success=False, error="OfficeCLI binary not found")
        except Exception as e:
            logger.exception("OfficeCLI exec failed")
            return OfficeResult(success=False, error=str(e))

    async def _run_cmd(self, cmd: list[str]) -> str:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode().strip()


# ── global singleton ───────────────────────────────────────────────────

_manager: OfficeManager | None = None


def get_office_manager() -> OfficeManager:
    """Return the global OfficeManager singleton."""
    global _manager
    if _manager is None:
        _manager = OfficeManager()
    return _manager
