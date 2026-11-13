import re
import sys


def count_words(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for word in re.findall(r"[a-z0-9]+", text.lower()):
        counts[word] = counts.get(word, 0) + 1
    return counts


def top_words(counts: dict[str, int], n: int = 5) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:n]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args:
        return 2
    try:
        with open(args[0], encoding="utf-8") as handle:
            text = handle.read()
    except OSError:
        return 1
    for word, count in top_words(count_words(text)):
        print(f"{word} {count}")
    return 0
