from __future__ import annotations

from pathlib import Path

from crabagent.core.agent.context import AgentContext
from crabagent.core.agent.tools.edit import edit_file
from crabagent.core.agent.tools.glob import _glob_to_regex, glob_files
from crabagent.core.agent.tools.grep import _expand_braces, _match_include, grep_files
from crabagent.core.agent.tools.read import read_file
from crabagent.core.agent.tools.write import write_file

# ── write ─────────────────────────────────────────────────────────────


def test_write_file_rejects_empty_path(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    result = write_file("", "content", context=context)
    assert "file_path is required" in result


def test_write_file_rejects_empty_content(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    result = write_file("a.txt", "", context=context)
    assert "content is required" in result


def test_write_file_creates_nested_dirs(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    result = write_file("sub/dir/a.txt", "hi", context=context)

    assert "Successfully wrote" in result
    assert (tmp_path / "sub" / "dir" / "a.txt").read_text() == "hi"


# ── edit ──────────────────────────────────────────────────────────────


def test_edit_file_reports_not_found(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "missing.txt"
    result = edit_file(str(target), "a", "b", context=context)
    assert "does not exist" in result


def test_edit_file_ambiguous_match(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "dup.txt"
    target.write_text("dup\ndup\n", encoding="utf-8")
    result = edit_file(str(target), "dup", "ok", context=context)
    assert "found 2 times" in result


def test_edit_file_old_string_not_found(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "f.txt"
    target.write_text("hello world", encoding="utf-8")
    result = edit_file(str(target), "nope", "x", context=context)
    assert "not found" in result


def test_edit_file_success_reports_line_number(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "f.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = edit_file(str(target), "line2", "replaced", context=context)

    assert "L2" in result
    assert "replaced" in target.read_text()


# ── read ──────────────────────────────────────────────────────────────


def test_read_file_nonexistent(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    result = read_file("nope.txt", context=context)
    assert "does not exist" in result


def test_read_file_directory_lists_entries(tmp_path: Path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    context = AgentContext(workspace=tmp_path)
    result = read_file(".", context=context)

    assert "a.txt" in result
    assert "sub/" in result


def test_read_file_offsets_and_limits(tmp_path: Path):
    target = tmp_path / "lines.txt"
    target.write_text("\n".join(f"line{i}" for i in range(1, 11)) + "\n", encoding="utf-8")
    context = AgentContext(workspace=tmp_path)
    result = read_file("lines.txt", offset=3, limit=2, context=context)

    assert "line3" in result
    assert "line4" in result
    assert "line5" not in result


def test_read_file_handles_binary(tmp_path: Path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01\x02\x00")
    context = AgentContext(workspace=tmp_path)
    result = read_file("blob.bin", context=context)

    assert "Binary file" in result


def test_read_file_returns_image_blocks_for_vision_model(tmp_path: Path):
    from PIL import Image

    target = tmp_path / "sample.png"
    Image.new("RGB", (20, 10), "red").save(target)
    context = AgentContext(workspace=tmp_path, model="gpt-4o")

    result = read_file("sample.png", context=context)

    assert isinstance(result, list)
    assert result[1]["type"] == "image_url"
    assert result[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result[1]["file_path"] == str(target)


def test_read_file_keeps_image_text_only_for_non_vision_model(tmp_path: Path):
    from PIL import Image

    target = tmp_path / "sample.png"
    Image.new("RGB", (20, 10), "red").save(target)
    context = AgentContext(workspace=tmp_path, model="deepseek-chat")

    result = read_file("sample.png", context=context)

    assert isinstance(result, str)
    assert "cannot receive this image directly" in result


# ── glob ──────────────────────────────────────────────────────────────


def test_glob_to_regex_matches_recursive():
    pattern = _glob_to_regex("**/*.py")
    assert pattern.match("a/b/c.py")
    assert pattern.match("x.py")
    assert not pattern.match("a/b/c.txt")


def test_glob_to_regex_brace_expansion():
    pattern = _glob_to_regex("*.{ts,tsx}")
    assert pattern.match("a.ts")
    assert pattern.match("b.tsx")
    assert not pattern.match("c.js")


def test_glob_files_finds_matches(tmp_path: Path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("z")
    context = AgentContext(workspace=tmp_path)

    result = glob_files("**/*.py", context=context)

    assert "a.py" in result
    assert "sub/c.py" in result
    assert "b.txt" not in result


def test_glob_files_returns_error_for_file_path(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("x")
    context = AgentContext(workspace=tmp_path)
    result = glob_files("*", path=str(target), context=context)
    assert "is a file, not a directory" in result


def test_glob_files_reports_no_matches(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    result = glob_files("*.nonexistent", context=context)
    assert result == "No files found matching pattern."


# ── grep ──────────────────────────────────────────────────────────────


def test_expand_braces_handles_plain_and_grouped():
    assert _expand_braces("*.py") == ["*.py"]
    assert _expand_braces("*.{ts,tsx}") == ["*.ts", "*.tsx"]


def test_match_include():
    assert _match_include("a.ts", ["*.ts"])
    assert not _match_include("a.js", ["*.ts"])


def test_match_include_bare_pattern_matches_nested_basename():
    # regression: bare globs keep matching the filename only, as before
    assert _match_include("a.py", ["*.py"], "pkg/sub/a.py")
    assert not _match_include("a.txt", ["*.py"], "pkg/a.py")


def test_match_include_brace_expansion_still_works():
    patterns = _expand_braces("*.{ts,tsx}")
    assert _match_include("a.tsx", patterns, "pkg/a.tsx")
    assert not _match_include("a.js", patterns, "pkg/a.js")


def test_match_include_path_globs():
    # "**/" spans any number of directories, including none
    assert _match_include("a.py", ["src/**/*.py"], "src/a.py")
    assert _match_include("a.py", ["src/**/*.py"], "src/pkg/a.py")
    assert not _match_include("b.py", ["src/**/*.py"], "tests/b.py")
    # "*" stays within one path segment
    assert _match_include("a.py", ["src/*.py"], "src/a.py")
    assert not _match_include("a.py", ["src/*.py"], "src/pkg/a.py")
    # "?" matches one non-slash character
    assert _match_include("a.py", ["src/?.py"], "src/a.py")
    assert not _match_include("ab.py", ["src/?.py"], "src/ab.py")


def test_match_include_path_glob_accepts_leading_dot_slash():
    for rel in ("src/a.py", "src/pkg/a.py", "tests/b.py"):
        assert _match_include("a.py", ["./src/**/*.py"], rel) == _match_include(
            "a.py", ["src/**/*.py"], rel
        )
    assert _match_include("a.py", ["./src/**/*.py"], "src/pkg/a.py")


def test_grep_files_single_file_mode(tmp_path: Path):
    target = tmp_path / "f.txt"
    target.write_text("hello\nworld\nfoo\n", encoding="utf-8")
    context = AgentContext(workspace=tmp_path)
    result = grep_files("world", str(target), context=context)

    assert "world" in result
    assert ":2:" in result


def test_grep_files_invalid_regex(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    result = grep_files("[unclosed", context=context)
    assert "invalid regex" in result


def test_grep_files_directory_mode(tmp_path: Path):
    (tmp_path / "a.py").write_text("import os\nprint('hi')\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("no match here\n", encoding="utf-8")
    context = AgentContext(workspace=tmp_path)

    result = grep_files("import", path=".", include="*.py", context=context)

    assert "a.py" in result
    assert "import os" in result


def test_grep_files_nonexistent_path(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    result = grep_files("x", path="./nonexistent", context=context)
    assert "does not exist" in result


def test_grep_files_include_directory_scoped_glob(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "nested.py").write_text("needle = 1\n", encoding="utf-8")
    (tmp_path / "pkg" / "skip.txt").write_text("needle = 2\n", encoding="utf-8")
    context = AgentContext(workspace=tmp_path)

    result = grep_files("needle", path=".", include="**/*.py", context=context)

    assert "nested.py" in result
    assert "skip.txt" not in result


# ── edit: replace_all and input validation ────────────────────────────


def test_edit_file_replace_all_replaces_every_occurrence(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "names.py"
    target.write_text("old_name = 1\nprint(old_name)\nreturn old_name\n", encoding="utf-8")

    result = edit_file(str(target), "old_name", "new_name", replace_all=True, context=context)

    assert "replaced 3 occurrences" in result
    assert "first @ L1" in result
    assert target.read_text(encoding="utf-8") == "new_name = 1\nprint(new_name)\nreturn new_name\n"


def test_edit_file_ambiguous_match_points_to_replace_all(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "dup.txt"
    target.write_text("dup\ndup\n", encoding="utf-8")

    result = edit_file(str(target), "dup", "ok", context=context)

    assert "replace_all=true" in result
    assert target.read_text(encoding="utf-8") == "dup\ndup\n"


def test_edit_file_rejects_empty_or_identical_strings(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "f.txt"
    target.write_text("hello", encoding="utf-8")

    assert "must not be empty" in edit_file(str(target), "", "x", context=context)
    assert "identical" in edit_file(str(target), "hello", "hello", context=context)
    assert target.read_text(encoding="utf-8") == "hello"


# ── grep: ignore_case and context_lines ───────────────────────────────


def test_grep_files_ignore_case(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("Hello World\nhello again\nbye\n", encoding="utf-8")

    assert grep_files("HELLO", path=str(target), context=context) == "No matches found."
    result = grep_files("HELLO", path=str(target), ignore_case=True, context=context)

    assert ":1: Hello World" in result
    assert ":2: hello again" in result
    assert "bye" not in result


def test_grep_files_context_lines_single_file(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("\n".join(f"line{i}" for i in range(1, 21)) + "\n", encoding="utf-8")

    result = grep_files("line(5|15)$", path=str(target), context_lines=1, context=context)
    lines = result.splitlines()

    assert lines == [
        f"{target}-4- line4",
        f"{target}:5: line5",
        f"{target}-6- line6",
        "--",
        f"{target}-14- line14",
        f"{target}:15: line15",
        f"{target}-16- line16",
    ]


def test_grep_files_context_lines_merges_overlapping_groups(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("a\nhit one\nb\nhit two\nc\n", encoding="utf-8")

    result = grep_files("hit", path=str(target), context_lines=1, context=context)

    assert "--" not in result
    assert result.splitlines() == [
        f"{target}-1- a",
        f"{target}:2: hit one",
        f"{target}-3- b",
        f"{target}:4: hit two",
        f"{target}-5- c",
    ]


def test_grep_files_context_lines_directory_mode(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    (tmp_path / "one.py").write_text("x = 1\nneedle = 2\ny = 3\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("needle = 9\n", encoding="utf-8")

    result = grep_files("needle", path=str(tmp_path), include="*.py", context_lines=1, context=context)

    assert f"{tmp_path / 'one.py'}:2: needle = 2" in result
    assert f"{tmp_path / 'one.py'}-1- x = 1" in result
    assert f"{tmp_path / 'two.py'}:1: needle = 9" in result
    assert "--" in result.splitlines()


def test_grep_files_default_output_is_unchanged(tmp_path: Path):
    context = AgentContext(workspace=tmp_path)
    target = tmp_path / "a.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")

    assert grep_files("beta", path=str(target), context=context) == f"{target}:2: beta"


# ── tool descriptions: model-facing guidance must not regress ──────────


def _tool_def(name: str) -> dict:
    from crabagent.core.agent.tools.registry import registry

    return next(d for d in registry.tool_defs() if d["function"]["name"] == name)


def test_grep_description_distinguishes_contents_from_file_names():
    function = _tool_def("grep")["function"]

    assert "to find files by name or path, use glob" in function["description"]
    include = function["parameters"]["properties"]["include"]["description"]
    # include is a path glob, not a second regex over file contents.
    assert "Glob pattern matched against file paths" in include
    assert "not file content, not a regex" in include
    assert '"*.py"' in include
    # directory-scoped globs are supported too.
    assert '"src/**/*.py"' in include
    # the content search is the regex.
    assert "always a regular expression, not a glob" in include


def test_write_description_points_existing_files_at_edit():
    description = _tool_def("write")["function"]["description"]

    assert "prefer edit" in description
    assert "replaces the whole file" in description
    assert "silently discards anything not included in the new content" in description
