---
name: python-debugger
description: Debug Python failures with Python-specific techniques - reading tracebacks, fast pytest feedback loops, non-interactive state inspection, hangs, warnings, import and async problems. Use for Python exceptions, failing pytest runs, hangs or wrong results in Python code; pair it with root-cause-debugging for the overall method.
---

# Python Debugger

This skill covers the Python-specific mechanics. For the overall method (reproduce, test hypotheses, find the root cause, apply a minimal fix), follow the `root-cause-debugging` skill if it is available. The techniques below plug into its steps.

## Use the project's interpreter
Run everything through the project's own environment, or you will debug the wrong installation:
- `.venv/bin/python` and `.venv/bin/pytest`
- `uv run …` or `poetry run …`, if the project uses them

The `python-env` skill covers environment setup. To confirm which copy of a module is actually imported:

```bash
python -c "import pkg, sys; print(pkg.__file__, getattr(pkg, '__version__', '?'), sys.version)"
```

## Fast feedback with pytest
Commands run through `bash` are non-interactive: `pdb`, `breakpoint()`, `pytest --pdb` and `input()` will hang or fail. Use these instead:

| Goal | Command |
|---|---|
| Stop at the first failure, show locals | `pytest path/to/test_x.py -x -q --tb=long -l` |
| Run one test | `pytest "path/to/test_x.py::TestClass::test_name" -q` |
| Rerun only last run's failures | `pytest --lf -x -q` |
| Select tests by keyword | `pytest -k "parse and not slow" -q` |
| See prints and logs | `pytest -s -o log_cli=true --log-cli-level=DEBUG …` |
| Make a warning fail loudly | `pytest -W error::DeprecationWarning …` |
| Dump stacks of a hanging test | `pytest -o faulthandler_timeout=30 …` |
| Find test-order pollution | run the failing test alone, then the whole file, and compare |

For scripts:
- `python -X dev script.py` turns on extra runtime checks: resource warnings, asyncio debug mode, and more.
- `python -X faulthandler script.py` prints tracebacks on crashes.
- Wrap anything that might hang in `timeout 60 …`.

## Read the traceback properly
- Read bottom-up. The last line gives the exception type and message. The last frame in *your* code (not `site-packages`) is usually the place to start.
- "The above exception was the direct cause of the following exception": the first traceback is the underlying cause.
- "During handling of the above exception, another exception occurred": the error handler itself failed, and the first exception is usually the real problem.
- When the exception comes from inside a library, look at the frame where your code called the library and check the arguments it passed.

## Inspect state without a debugger
- Add a temporary `print(f"{value=}")` (the `=` prints both the name and the value) or a `logging.debug` line. Remove it afterwards.
- Probe the function directly with the failing input:

```bash
python - <<'EOF'
from mypkg.pricing import parse_price
print(repr(parse_price("-0,50")))
EOF
```

- Check types and shapes, not just values: `type(x)`, `len(x)`, `x.keys()`, `df.dtypes`.

## Python-specific suspects
Check these when the cause isn't obvious. Each one is explained, with how to confirm it, in [references/common-fixes.md](references/common-fixes.md).
- Mutable default arguments, and closures in loops that capture the variable rather than its value.
- `is` versus `==`, and truthiness: `0`, `""` and `[]` are falsy, but they are not `None`.
- A project file shadowing a module name (`random.py`, `json.py`, `test.py`).
- Circular imports ("partially initialized module").
- Async: a missing `await` ("coroutine was never awaited"), a blocking call inside async code, mixing event loops.
- Naive versus timezone-aware datetimes; floats used for money.
- Missing `encoding="utf-8"` when reading or writing text.
- Changing a list or dict while iterating over it.

## Fix and verify
If your role or brief says not to change code (a tester or reviewer), stop here: report the root cause, the evidence and where the fix belongs.

1. Fix the cause, not the symptom, and keep the diff minimal.
2. Rerun the exact command that failed, then the test file, then the wider suite if it's cheap.
3. Add a regression test that fails without the fix (see `proportional-testing`).
4. Remove every temporary print, log line and probe script before reporting.
