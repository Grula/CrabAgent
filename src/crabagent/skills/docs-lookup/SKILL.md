---
name: docs-lookup
description: Check how a library, language feature or API really behaves before relying on it - the installed version's source and docs first, then the official reference (cppreference, docs.python.org, MDN), or ask a teammate with web access. Use when unsure of a signature, edge case or version support.
---

# Docs Lookup

A guessed API becomes a bug that someone else has to find. When you aren't sure
how something behaves, look it up, and look it up for the version this project
uses.

## 1. The installed version first
It is fast, works offline, and matches exactly what the project runs.

| Ecosystem | Where to look |
|---|---|
| Python | `python -c "import pkg; help(pkg.func)"`, `inspect.getsource(...)`, the package under `site-packages`, `pkg.__version__` |
| JavaScript / TypeScript | `node_modules/<pkg>/package.json` for the version, its `.d.ts` type definitions, its README |
| C / C++ | the headers the project includes, `man 3 <function>`, the compiler version (`c++ --version`) and the standard in the build flags (`-std=c++20`) |
| Go | `go doc <pkg>.<Symbol>` |
| Rust | the crate source under `~/.cargo/registry/src/` for the version in `Cargo.lock` |
| Command-line tools | `<tool> --help`, `man <tool>` |

`grep` the dependency for the symbol and read only that function and its comments.

## 2. The official reference page
When the installed code doesn't settle it (defined behaviour, complexity,
guarantees, which standard or version added it), go to the authoritative source:
- C / C++: cppreference.com (watch the "since C++XX" markers)
- Python: docs.python.org, for the project's Python version
- Web platform: MDN
- Go: pkg.go.dev. Rust: docs.rs, for the exact version
- Libraries: their official documentation for the version in use

Tutorials, blog posts and Q&A answers can point you somewhere; they are not
evidence.

If you have `web_scrape` or `web_search`, open the page yourself. If you don't, ask
a teammate who does, with `request_help`. Use `researcher`, or whoever your prompt
lists with web access:

```
request_help("researcher", "From cppreference.com, for C++20: what does
std::span::subspan(offset, count) do when offset + count > size()? Quote the
signature and the sentence that answers it, with the URL.")
```

One precise question per call. Name the source and the version, and ask for a
quote, not a summary.

## 3. Use the answer
- Cite it in your report, for example: "per cppreference, `subspan` is undefined
  behaviour if `Offset > size()`".
- If the docs and the installed code disagree, the installed code is what runs
  here. Follow it and mention the mismatch.
- If you still can't confirm it, say so in your report as an open question
  instead of guessing.
