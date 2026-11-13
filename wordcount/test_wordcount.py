"""Contract tests for the wordcount module.

Pins the interface specified by the Architect:

    count_words(text: str) -> dict[str, int]
    top_words(counts: dict[str, int], n: int = 5) -> list[tuple[str, int]]
    main(argv: list[str] | None = None) -> int
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import wordcount  # noqa: E402  (import after sys.path setup)


# BR1
def test_count_words_lowercases_and_tokenizes():
    assert wordcount.count_words("Hello, World! HELLO") == {"hello": 2, "world": 1}


# BR2
def test_count_words_empty_returns_empty():
    assert wordcount.count_words("") == {}
    assert wordcount.count_words("... !!!") == {}


# BR3
def test_top_words_sorts_count_then_alpha():
    assert wordcount.top_words({"b": 2, "a": 2, "c": 1}) == [
        ("a", 2),
        ("b", 2),
        ("c", 1),
    ]


# BR4
def test_top_words_limits_to_n():
    assert wordcount.top_words({"a": 3, "b": 2, "c": 1}, n=2) == [("a", 3), ("b", 2)]


# BR5
def test_top_words_fewer_than_n():
    assert wordcount.top_words({"a": 1, "b": 1}, n=5) == [("a", 1), ("b", 1)]


# BR6
def test_main_prints_top5_and_returns_0(tmp_path, capsys):
    # delta 5, bravo 4, echo 3, alpha 2, charlie 1, foxtrot 1
    # -> top 5 drops "foxtrot" (tied with "charlie", loses on alphabetical order)
    text = (
        "Delta delta DELTA delta delta "
        "bravo Bravo bravo bravo "
        "echo echo ECHO "
        "alpha alpha "
        "charlie "
        "foxtrot"
    )
    path = tmp_path / "input.txt"
    path.write_text(text, encoding="utf-8")

    result = wordcount.main([str(path)])

    captured = capsys.readouterr()
    assert captured.out.strip().splitlines() == [
        "delta 5",
        "bravo 4",
        "echo 3",
        "alpha 2",
        "charlie 1",
    ]
    assert result == 0


# BR7
def test_main_missing_arg_returns_2():
    assert wordcount.main([]) == 2


# BR8
def test_main_nonexistent_file_returns_1(tmp_path):
    missing = tmp_path / "no_such_file.txt"
    assert not missing.exists()
    assert wordcount.main([str(missing)]) == 1
