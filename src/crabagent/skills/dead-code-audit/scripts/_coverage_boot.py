"""Line recorder that py_coverage.py loads into every Python process it measures.

py_coverage.py copies this file into a temporary folder as ``sitecustomize.py`` and
puts that folder first on PYTHONPATH, so Python imports it at start-up. It does
nothing unless PYCOV_OUT is set. Then it:

- records which lines of the files under PYCOV_SRC run (sys.monitoring on Python
  3.12+, sys.settrace before that, or when PYCOV_TRACER=settrace),
- writes them to PYCOV_OUT/cov-<pid>-<random>.json at exit, and on SIGTERM when
  the program has no SIGTERM handler of its own (then dies of SIGTERM as before),
- passes itself on to Python subprocesses, even ones started with their own env,
- and finally runs the sitecustomize it hides, if there is one.

Standard library only. A process killed with SIGKILL or ended by os._exit()
writes nothing.
"""
import os
import sys


def _start() -> None:
    out_dir = os.environ.get("PYCOV_OUT")
    if not out_dir:
        return
    import atexit
    import json
    import uuid

    roots = tuple(
        os.path.join(os.path.realpath(p), "")
        for p in os.environ.get("PYCOV_SRC", "").split(os.pathsep)
        if p
    )
    boot_dir = os.path.dirname(os.path.abspath(__file__))
    hits: dict = {}
    keys: dict = {}

    def key_for(filename: str):
        """The real path of a measured file, or None for files outside PYCOV_SRC."""
        try:
            return keys[filename]
        except KeyError:
            real = os.path.realpath(filename)
            key = real if real.startswith(roots) and real.endswith(".py") else None
            keys[filename] = key
            return key

    monitoring = getattr(sys, "monitoring", None)
    if os.environ.get("PYCOV_TRACER") == "settrace":
        monitoring = None
    if monitoring is not None:
        tool = monitoring.COVERAGE_ID
        try:
            monitoring.use_tool_id(tool, "py_coverage")
        except ValueError:  # another coverage tool holds the slot
            monitoring = None
    if monitoring is not None:
        disable = monitoring.DISABLE

        def on_line(code, line):
            key = key_for(code.co_filename)
            if key is not None:
                hits.setdefault(key, set()).add(line)
            return disable  # each location reports once, so measuring stays cheap

        monitoring.register_callback(tool, monitoring.events.LINE, on_line)
        monitoring.set_events(tool, monitoring.events.LINE)
    else:
        import threading

        def tracer(frame, event, arg):
            key = key_for(frame.f_code.co_filename)
            if key is None:
                return None
            lines = hits.setdefault(key, set())

            def local(frame, event, arg):
                if event == "line":
                    lines.add(frame.f_lineno)
                return local

            return local

        sys.settrace(tracer)
        threading.settrace(tracer)

    saved = []

    def save() -> None:
        if saved:
            return
        saved.append(True)
        try:
            path = os.path.join(out_dir, f"cov-{os.getpid()}-{uuid.uuid4().hex[:8]}.json")
            with open(path + ".tmp", "w", encoding="utf-8") as fh:
                json.dump({k: sorted(v) for k, v in hits.items()}, fh)
            os.replace(path + ".tmp", path)
        except OSError:
            pass

    atexit.register(save)

    try:
        import signal

        if signal.getsignal(signal.SIGTERM) == signal.SIG_DFL:

            def on_term(signum, frame):
                save()
                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                os.kill(os.getpid(), signal.SIGTERM)

            signal.signal(signal.SIGTERM, on_term)
    except (ImportError, AttributeError, ValueError, OSError):
        pass  # no SIGTERM on this platform, or not the main thread

    # A test that starts a subprocess with its own env (often a replaced PYTHONPATH)
    # would drop this recorder; put it back in.
    import subprocess

    original_init = subprocess.Popen.__init__

    def init_with_recorder(self, *args, **kwargs):
        env = kwargs.get("env")
        if env is not None and all(isinstance(k, str) for k in env):
            env = dict(env)
            for name in ("PYCOV_OUT", "PYCOV_SRC", "PYCOV_TRACER"):
                if name in os.environ:
                    env.setdefault(name, os.environ[name])
            parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
            if boot_dir not in parts:
                env["PYTHONPATH"] = os.pathsep.join([boot_dir] + parts)
            kwargs["env"] = env
        original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = init_with_recorder


def _run_hidden_sitecustomize() -> None:
    """Run the sitecustomize this file shadows, so the environment behaves as usual."""
    import importlib.machinery
    import importlib.util

    here = os.path.dirname(os.path.abspath(__file__))
    path = [p for p in sys.path if os.path.abspath(p or os.curdir) != here]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", path)
    if spec is not None and spec.loader is not None:
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)


try:
    _start()
except Exception:  # measuring must never break the program under test
    pass
try:
    _run_hidden_sitecustomize()
except Exception:
    pass
