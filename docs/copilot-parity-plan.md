# GitHub Copilot Chat to CrabAgent: parity and hardening plan

Owner: System Architect. Last updated: 2026-11-25. This file is the durable copy of the approved plan; re-read it before planning any step.

Status legend: **done** | **partly** (what is missing is stated) | **not started** | **dropped** (reason stated).

> Reconstruction note: this document was rebuilt after a context compaction erased the original plan. Statements about Copilot Chat were verified against the source clone and are exact; the parity matrix is recorded at capability-family level, which is what the original concluded once the per-tool rows were collapsed. Two per-tool description alignments were carried as actions and are named in T1.

## 1. Sources and method

- `microsoft/vscode-copilot-chat`, shallow clone at commit `5863f5a` (2026-05-20). MIT licensed, archived; development moved into `microsoft/vscode`.
- `microsoft/vscode` for the MCP client surface.
- Public ACP and MCP documentation.
- No decompiling of closed-source extensions. Legacy inline-completion internals are out of scope: no parity value, and licensing risk.

## 2. What Copilot Chat actually does (verified, not assumed)

- 37 tools declared in `package.json` under `contributes.languageModelTools`, registered with `vscode.lm.registerTool(getContributedToolName(name), tool)` (`tools/vscode-node/tools.ts:36-39`).
- Arguments are validated with Ajv using `coerceTypes`, plus a repair pass that parses nested stringified JSON (`tools/common/toolsService.ts:140-246`).
- The loop (`intents/node/toolCallingLoop.ts`) is abstract; the round cap is injected (`chat.agent.maxRequests`, default 200). Stop / subagent-stop hooks can force another round. Cancellation is a single `CancellationToken`. Per-tool errors become retryable tool messages. There is **no** token budget in the loop, only a per-result truncation at `MAX_TOOL_RESPONSE_PCT = 0.5` of the window.
- MCP lives in VS Code core and is client-only. Neither VS Code nor CrabAgent implements ACP or an MCP server.

## 3. Architecture decision

Adapt, do not rebuild. CrabAgent already has the equivalent machinery: one `ToolRegistry` (~105 tools), one loop (`run_agent`), parallel tool execution, iteration budget plus grace summary, LLM compaction, sub-agent delegation with a permission-filtered registry, skills via a model-pulled tool, deferred tool groups, an MCP client. The gaps are three, not thirty: tool-contract hardening, two real capabilities (semantic code search, batch multi-file edit), and an MCP server surface.

## 4. Parity matrix (capability families)

| CrabAgent capability | Copilot Chat equivalent | Status | Action |
|---|---|---|---|
| `read`, `write`, `edit`, `glob`, `grep` | readFile / editFiles / fileSearch / textSearch | equivalent | Description alignment only (T1) |
| `bash`, `run_tests` | runInTerminal / testFailure | equivalent | none |
| Sub-agent delegation | runSubagent / searchSubagentTool | equivalent | none |
| Todo and task tracking | manageTodoList / taskComplete | equivalent | none |
| Skills via tool search | toolSearch / skills | equivalent | none |
| MCP client | VS Code MCP client | equivalent | none |
| Browser and vision tools | openSimpleBrowser / screenshot | equivalent | none |
| Semantic code search | semantic search over the workspace index | **done** | Symbol + FTS index, `code_search` tool (T3) |
| Batch multi-file edit | editFiles with multiple edits, one approval | **done** | `replacements` array on `edit` (T2) |
| Workspace tree in context | implicit workspace listing | **done** | Size-capped tree is in the system prompt (measured cost in T1) |
| Diff preview on approval | edit approval shows a diff | **done** | Built and persisted, verified live (T1) |
| Per-tool argument contract | Ajv plus coercion | **done** | T1 |
| In-loop cancellation | CancellationToken | **done** | T4, extended to running tools |
| Stop / continuation hook | stop and subagent-stop hooks | **done** | T4 |
| Before-tool hook | pre-tool policy hook | **done** | added during T4 follow-up |
| MCP server surface (expose CrabAgent tools) | not offered | **done** | Read-only stdio server, `--mcp-server` (T5) |
| Code editor UI | VS Code itself | **done** (CrabAgent-specific) | T6 |
| Result truncation by window fraction | `MAX_TOOL_RESPONSE_PCT` | **dropped** | See section 7 |

## 5. Integration decision matrix

| Option | Verdict | Why |
|---|---|---|
| In-process pattern parity (adapt CrabAgent) | **primary** | No new runtime, no TypeScript, no `vscode.*` coupling |
| MCP server mode (CrabAgent as server) | **secondary** | Lets other clients use CrabAgent tools; read-only allowlist keeps it safe |
| Vendoring Copilot modules | rejected | TypeScript bound to `vscode.*`; useless outside the extension host |
| ACP server | deferred | No mature consumer for this use case yet |

## 6. Reuse versus reimplement

Reuse (port the idea, write the Python): schema validation with coercion and repair, stop/before-tool hook shape, cancellation boundaries, per-result truncation policy, diff-before-approval.

Reimplement nothing wholesale: no Copilot module is portable, so every item above is a design port, not a code copy.

Never propose a tool that duplicates an existing CrabAgent capability; every task below either hardens an existing seam or adds a capability the matrix marks missing.

## 7. Tasks

### T1 - Tool contract hardening
Deliverables: argument repair plus JSON-Schema validation at the registry boundary; actionable error strings; two description alignments (`grep.include` is a path glob while `pattern` is always a regex; `write` steers existing files to `edit`); size-capped workspace tree in the system prompt; diff preview on write/edit confirmations.
Acceptance: a wrong-typed argument yields one actionable message naming the argument and the handler is not called; every valid call behaves exactly as before; the confirm card shows the pending diff; the tree costs a bounded number of tokens.
Status: **done** — validation, coercion, both description alignments, the diff preview (including persistence, so a reloaded confirmation shows it), directory-scoped `include` globs and the size-capped workspace tree in the system prompt are built and verified. The tree costs a measured 176 entries, 3951 characters, about 990 tokens for this repository; the cap is 200 entries / 4000 chars, set by `workspace_tree_max_entries` (0 disables it).

### T2 - Batch multi-file edit
Deliverables: optional `replacements` list on `edit`, applied as one unit with one permission prompt, one molt snapshot for the batch, per-item results.
Acceptance: a batch of N edits produces one confirmation, one snapshot, N per-item outcomes, and never a partially applied file; failures are reported per item without aborting the rest.
Status: **done** — optional `replacements` array on `edit`, applied in order in memory with one write, one confirmation and one molt snapshot; per-item result lines report each change; 10 tests.

### T3 - Semantic code search
Deliverables: an index over the workspace and a search tool that returns ranked locations.
Acceptance: relevant hits for natural-language queries; bounded build time and memory; incremental update on file change.
Status: **done** — a symbol and FTS index in its own WAL database plus the `code_search` tool; measured numbers are in section 10.

### T4 - Agent loop hardening
Deliverables: provider-failure reporting for every failure path (pre-loop setup, mid-loop, exceptions escaping the run task); cooperative cancellation checked at iteration boundaries and between tool calls; mid-tool cancellation that kills a running tool's process group; stop/continuation hook; before-tool hook.
Acceptance: every provider failure yields one short actionable message naming the provider and model; a cancel stops within one iteration, emits no error and leaves a valid message history; a running `bash` or `run_tests` dies with its whole process group; hooks are additive and change nothing when unimplemented.
Status: **done**. Dropped from T4: run-level token budget (see section 8) and window-fraction truncation. A cancel that lands inside `bash`'s timeout window now kills the process group instead of reporting the job moved to the background.

### T5 - MCP server mode
Deliverables: `crabagent --mcp-server` exposing the read-only tool allowlist over MCP; mutating tools refused.
Acceptance: a real MCP client session lists the allowlisted tools and calls one successfully; a mutating tool call is refused with a clear error; no filesystem write is possible through the server.
Status: **done** — `crabagent --mcp-server`, a dependency-free stdio JSON-RPC server, exposes an allowlist of exactly `read`, `glob` and `grep`; every other tool is refused before a handler runs, and path escapes are contained. Verified with a real MCP SDK client session (handshake, list, read, refused write that wrote nothing); 21 tests.

### T6 - Editor UI and agent integration
Deliverables: CodeMirror 6 editor in the Work tab, file tree, tabs, save, backend tree/read/write endpoints, `file_updated` sync without losing the cursor, active file injected into agent context, dirty-buffer banner (Reload / Show diff) with no auto-merge, graceful read-only state for binary and oversized files.
Acceptance: open a file, edit, save, see it on disk; an agent edit updates the buffer without moving the cursor; a dirty buffer is never silently reloaded.
Status: **done** — built and browser-verified; both tab holes are closed, and the Markdown preview 404 is fixed: `MarkdownPanel` now sends `isAbsolutePath(path)` plus the session id (4 unit tests). Known gap, tracked separately: Markdown **save** still omits `absolute`/`session_id`.

## 8. Dropped work and why

| Item | Reason |
|---|---|
| Run-level input-token budget | The plan said not to add it. Built during T4 by mistake, then shipped disabled, then removed entirely at the user's request. Most input tokens are cache reads, so a fixed threshold trips on legitimate long runs. |
| Window-fraction tool truncation (0.5 x window) | With a 1M-token window this allows roughly 500k-token tool results that every later step re-sends. The fixed 20k-character cap stays. |
| Vendoring Copilot modules | Bound to the VS Code extension host. |
| Legacy inline-completion internals | Out of scope: no parity value, licensing risk. |

## 9. Open questions

1. **T3 implementation choice**: local embeddings versus a symbol/RAG-lite index. The user **accepted the symbol/RAG-lite recommendation**, and it is now built (section 10): a symbol/RAG-lite index over local embeddings, because no embedding runtime is installed (only numpy) while FTS5 is already in the database, a symbol index builds in seconds against roughly 30-60 s for embeddings on CPU, and identifier-level queries are what a coding agent mostly issues. Numbers: 632 files / 137k lines to index, about 3.2k chunks at 40 lines, roughly 5 MB of 384-dim vectors, versus a symbol index build under 5 s with no new dependency.
2. **Resolved** — verified on a preview server with one real `deepseek-flash` model call: the confirm-gated `edit` showed its diff (`+1`/`-1`, correct path) and the diff was still present after a page reload.
3. Binary and oversized files in the editor: currently read-only and truncated; decide whether any of that should be surfaced to the agent as a distinct tool result.
4. `grep.include` now expresses directory-scoped globs: a pattern containing `**` spans directories (`src/**/*.py` matches `src/a.py`), and bare filename globs such as `*.py` behave exactly as before.
5. **Resolved** — no pruning for stale workspaces: `prune_stale_workspaces()` deletes the rows (files, symbols, chunks, FTS entries, `last_build:` meta) of workspaces whose folder is gone. It is lazy (called from `ensure_indexed`, at most once per process unless `force=True`), lock-aware (a workspace with a build in flight is skipped, never waited for), skips the workspace being built, and commits one transaction per workspace so one failure cannot block the rest.

## 10. T3 design (symbol / RAG-lite index)

**Status: built.** `crabagent/core/code_index` (storage and extraction) plus the `code_search` tool implement this design. Measured on this repository: cold build 1.52 s, warm `ensure_indexed` 16.8 ms, 634 files / 7549 symbols / 2956 chunks, index file 12.6 MB, query latency 26–53 ms. Tests: 25 for storage and extraction, 23 for the tool and ranking.

Decision: a symbol and text index over the workspace, built from SQLite FTS5 with no new dependency. Local embeddings are deferred; `core/memory_embed.py` already shows the guarded shape such a path would take.

### What gets indexed

- Files whose extension is in an explicit allowlist: `.py .pyi .ts .tsx .js .jsx .md .toml .yaml .yml .json .sh .sql`, under the workspace, skipping the same noise directories as the workspace tree (`.git`, `node_modules`, `dist`, `build`, `__pycache__`, virtualenvs, caches, `static/assets`).
- Files larger than 512 KB and files with a NUL byte in the first 4 KB are skipped (binary or generated).
- Two record kinds per file:
  - **symbols** — Python via `ast` (functions, async functions, classes, methods, module level constants), TypeScript and JavaScript via a documented regex pass (classes, functions, exported consts, interfaces, types), Markdown via headings. Each symbol row carries its kind, name, qualified name, line range, signature line and leading docstring/comment.
  - **text** — the file body, chunked at 60 lines with 10 lines of overlap, for full-text matching inside function bodies.

Scale for this repository: 632 files, 137k lines, roughly 4k symbols and 3k text chunks.

### Tables

```
code_files(id, workspace, path, lang, mtime_ms, size, sha1, indexed_at)
  unique(workspace, path)
code_symbols(id, file_id -> code_files.id, kind, name, qualname, start_line, end_line, signature, doc)
code_chunks(id, file_id -> code_files.id, start_line, end_line, text)
code_symbols_fts  -- FTS5, external content over code_symbols(name, qualname, signature, doc)
code_chunks_fts   -- FTS5, external content over code_chunks(text)
code_index_meta(key, value)  -- schema version, last full build, workspace fingerprint
```

No migration framework is used: new tables need only the model classes, since `create_all` builds them. The FTS5 tables and their triggers are created in `init_db()` the same way `messages_fts_cjk` already is.

Where the tables live was decided by the user, overriding the original draft: the index is its **own SQLite file**, `~/.crabagent/code_index.db`, in **WAL** mode, opened and created by the index module itself and never in `crabagent.db`, because code text plus FTS per workspace would bloat the main database and a bulk build would hold its single write lock while chats and events are being saved; deleting the file simply causes a rebuild.

### How FTS5 is reused

`messages_fts_cjk` already proves the pattern in this codebase: an FTS5 table with `content=` pointing at a base table plus insert/update/delete triggers, queried with `MATCH`. The code indexes copy that shape with the `unicode61` tokenizer — appropriate for identifiers because it keeps `_` and `.` out of the token stream only where desired; the query is built by escaping tokens and joining them with `OR`, then ranked with SQLite's built-in `bm25()` (which the existing message search does not currently use). Ranking blends three signals: `bm25` of the matched text, an exact or prefix match on `code_symbols.name`/`qualname`, and a path depth/segment bonus so `src/` outranks vendored trees. Results are deduplicated per file, then per symbol, and capped at 10 by default.

### When it rebuilds

- **Lazy first build**: on the first `code_search` call for a workspace with no rows (or a schema-version mismatch), the full index builds before answering. Measured cost for this repository is under 5 seconds (ast over 56k Python lines is 1–2 s, the regex pass over 35k TypeScript lines about 1 s, the rest is I/O and FTS inserts).
- **Cheap staleness check per query**: `stat` every indexed file (`mtime_ms` + `size`); files whose stamp changed are re-parsed, deleted files are removed, new files are added. 632 `stat` calls are sub-10 ms, so this runs on every query without a debounce. If more than 200 files changed at once, it falls back to a full rebuild.
- **Explicit rebuild**: `code_search(reindex=true)` forces the full path; used after large checkouts or branch switches.
- **Never in the agent loop's critical path at startup**: the index is built on first use, not when the server boots, so a fresh workspace costs nothing until something asks.
- **One build per workspace at a time**: builds are serialized by an `asyncio.Lock` keyed by the resolved workspace path, so parallel callers wait for the running build and never start a second one.

### Tool interface

One tool, `code_search`, returning compact ranked locations (never whole files, so a result costs tens of tokens, not thousands):

```
code_search(query: str, path_prefix: str | None = None,
            kind: str | None = None,      # symbol kind filter: function|class|method|heading|text
            limit: int = 10, reindex: bool = False) -> str
```

Two decisions, as implemented: `reindex` stays a **parameter, not a separate tool**, and there is no separate "find definition" parameter — `kind="symbol"` with an exact-name match ranked first is that path.

Result shape, one line per hit plus a short snippet:

```
src/crabagent/core/agent/tools/registry.py:352-448  method ToolRegistry.execute  — "Return the permission-filtered..."
  def execute(self, name, arguments, context=None):
```

Deliberate limits: no vector search, no natural-language paraphrase matching, no cross-workspace search, and no caching of embeddings. If a query is paraphrastic the model should fall back to `grep`, which the tool description says explicitly so the model does not over-trust it.

Cost: roughly 300–500 lines of code plus tests, one new tool registration, five new tables, no new dependency, and a sub-5-second cold build for this repository.

### Resolved: the exact tier needs a strict majority

The exact-name tier used to win for any one matching token, so a multi-word query in which one word is itself a symbol name put that symbol first: the real run returned the function `workspace` for `workspace tree` and a skill script's `diff()` for `diff preview`. The tier now applies only when the query is a single token or the symbol name covers a **strict majority** of the query words — `matched / |query words| > 0.5`, not `>=`, because 1 of 2 is exactly half and must lose (that is precisely the `workspace` and `diff` case). Names are compared after splitting on dots, underscores and camelCase, so `build_workspace_tree` covers both words of `workspace tree` while `workspace` covers one. The prefix tier is gated by the same rule, otherwise it would simply take over the wrong results. Below the tiers nothing changed (negated bm25, path depth, path, line). Same run after the change: `workspace tree` -> `build_workspace_tree` in `workspace_tree.py`, `diff preview` -> `_stored_diff_preview` then `build_diff_preview`.

## 11. Changelog

- 2026-11-24: document created (reconstruction). T1 partly, T4 done, T6 partly. Token budget removed. Window-fraction truncation dropped.
- 2026-11-24: diff preview now persisted with the confirmation (backend) and restored on reload in the UI. Cancel-versus-auto-background race fixed: a cancel that lands inside `bash`'s timeout window kills the process group. `grep.include` accepts directory-scoped globs. T1 now misses only the size-capped workspace tree.
- 2026-11-24: full Python suite 1383 passing; frontend 222 tests passing.
- 2026-11-25: T1, T2, T5 and T6 done. Workspace tree in the system prompt (176 entries, 3951 chars, about 990 tokens here; cap 200 entries / 4000 chars). T2 batch replacements on `edit`: one write, one confirmation, one molt snapshot, per-item results. T5 `crabagent --mcp-server`: read-only stdio MCP server over `read`/`glob`/`grep`. T6 tab edge cases closed, plus the Markdown preview read fix (`isAbsolutePath(path)` and the session id); Markdown save still to do. Full Python suite 1434 passing; frontend 234 tests passing.
- 2026-11-25: T3 built — the code index lives in its own WAL database (`~/.crabagent/code_index.db`), builds are serialized by a per-workspace lock, and the `code_search` tool ranks by exact match, then prefix, then `bm25`. Measured cold build 1.52 s, warm `ensure_indexed` 16.8 ms, query latency 26–53 ms on this repository. 48 new tests (25 storage/extraction, 23 tool/ranking); Python suite 1483 passing, frontend 238 tests passing.
- 2026-11-25: `code_search` ranking tightened — the exact and prefix tiers now need a strict majority of the query words, which fixes the two multi-word cases above (`workspace tree` and `diff preview`). The seeded scout and coder goals route a definition, a symbol or a question of where something lives to `code_search` first (`kind="symbol"` for a definition by name), and keep `grep` for exact strings, error messages, regexes and paraphrased questions. 6 new tests (5 in `tests/test_code_search.py`, 1 in `tests/test_default_agent.py`); Python suite 1489 passing.
- 2026-11-25: stale workspaces are pruned from `code_index.db` — `prune_stale_workspaces()` removes the files/symbols/chunks/FTS rows and the `last_build:` meta of every workspace whose folder is gone. Lazy (the first `ensure_indexed()` of a process sweeps once, `force=True` again), lock-aware (a workspace with a build in flight is skipped, never waited for) and per-workspace transactional. Children are deleted explicitly before `code_files` so the `*_ad` triggers clear the external-content FTS index; a `MATCH` test pins that. 5 new tests in `tests/test_code_index.py` (30 there); Python suite 1494 passing.
