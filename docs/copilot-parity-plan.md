# GitHub Copilot Chat to CrabAgent: parity and hardening plan

Owner: System Architect. Last updated: 2026-11-24. This file is the durable copy of the approved plan; re-read it before planning any step.

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
| Semantic code search | semantic search over the workspace index | **missing** | Build (T3, on hold) |
| Batch multi-file edit | editFiles with multiple edits, one approval | **partial** | Add `replacements` to `edit` (T2) |
| Workspace tree in context | implicit workspace listing | **partial** | Size-capped tree in the system prompt (T1 leftover) |
| Diff preview on approval | edit approval shows a diff | **partial** | Built; persistence was the gap (T1) |
| Per-tool argument contract | Ajv plus coercion | **done** | T1 |
| In-loop cancellation | CancellationToken | **done** | T4, extended to running tools |
| Stop / continuation hook | stop and subagent-stop hooks | **done** | T4 |
| Before-tool hook | pre-tool policy hook | **done** | added during T4 follow-up |
| MCP server surface (expose CrabAgent tools) | not offered | **missing** | Build read-only mode (T5) |
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
Status: **partly** — validation, coercion, both description alignments, the diff preview (including persistence, so a reloaded confirmation shows it) and directory-scoped `include` globs are built and verified; the size-capped workspace tree is the only T1 item still missing.

### T2 - Batch multi-file edit
Deliverables: optional `replacements` list on `edit`, applied as one unit with one permission prompt, one molt snapshot for the batch, per-item results.
Acceptance: a batch of N edits produces one confirmation, one snapshot, N per-item outcomes, and never a partially applied file; failures are reported per item without aborting the rest.
Status: **not started**.

### T3 - Semantic code search
Deliverables: an index over the workspace and a search tool that returns ranked locations.
Acceptance: relevant hits for natural-language queries; bounded build time and memory; incremental update on file change.
Status: **not started**, deliberately on hold pending open question 1.

### T4 - Agent loop hardening
Deliverables: provider-failure reporting for every failure path (pre-loop setup, mid-loop, exceptions escaping the run task); cooperative cancellation checked at iteration boundaries and between tool calls; mid-tool cancellation that kills a running tool's process group; stop/continuation hook; before-tool hook.
Acceptance: every provider failure yields one short actionable message naming the provider and model; a cancel stops within one iteration, emits no error and leaves a valid message history; a running `bash` or `run_tests` dies with its whole process group; hooks are additive and change nothing when unimplemented.
Status: **done**. Dropped from T4: run-level token budget (see section 8) and window-fraction truncation. A cancel that lands inside `bash`'s timeout window now kills the process group instead of reporting the job moved to the background.

### T5 - MCP server mode
Deliverables: `crabagent --mcp-server` exposing the read-only tool allowlist over MCP; mutating tools refused.
Acceptance: a real MCP client session lists the allowlisted tools and calls one successfully; a mutating tool call is refused with a clear error; no filesystem write is possible through the server.
Status: **not started**.

### T6 - Editor UI and agent integration
Deliverables: CodeMirror 6 editor in the Work tab, file tree, tabs, save, backend tree/read/write endpoints, `file_updated` sync without losing the cursor, active file injected into agent context, dirty-buffer banner (Reload / Show diff) with no auto-merge, graceful read-only state for binary and oversized files.
Acceptance: open a file, edit, save, see it on disk; an agent edit updates the buffer without moving the cursor; a dirty buffer is never silently reloaded.
Status: **partly** — built and browser-verified, except two tab edge cases: closing every code tab leaves a code chip that opens an empty editor, and `closeActiveDoc` shows an empty state instead of falling back to the code chip.

## 8. Dropped work and why

| Item | Reason |
|---|---|
| Run-level input-token budget | The plan said not to add it. Built during T4 by mistake, then shipped disabled, then removed entirely at the user's request. Most input tokens are cache reads, so a fixed threshold trips on legitimate long runs. |
| Window-fraction tool truncation (0.5 x window) | With a 1M-token window this allows roughly 500k-token tool results that every later step re-sends. The fixed 20k-character cap stays. |
| Vendoring Copilot modules | Bound to the VS Code extension host. |
| Legacy inline-completion internals | Out of scope: no parity value, licensing risk. |

## 9. Open questions

1. **T3 implementation choice** (blocking T3): local embeddings versus a symbol/RAG-lite index. Needs cost, build time for this repository and a recommendation before any code.
2. Please confirm, in your own live check after a restart, that a reloaded confirmation still shows the diff. The persistence is implemented and covered by tests; the live confirm-gated edit is what remains to be seen.
3. Binary and oversized files in the editor: currently read-only and truncated; decide whether any of that should be surfaced to the agent as a distinct tool result.
4. `grep.include` now expresses directory-scoped globs: a pattern containing `**` spans directories (`src/**/*.py` matches `src/a.py`), and bare filename globs such as `*.py` behave exactly as before.

## 10. Changelog

- 2026-11-24: document created (reconstruction). T1 partly, T4 done, T6 partly. Token budget removed. Window-fraction truncation dropped.
- 2026-11-24: diff preview now persisted with the confirmation (backend) and restored on reload in the UI. Cancel-versus-auto-background race fixed: a cancel that lands inside `bash`'s timeout window kills the process group. `grep.include` accepts directory-scoped globs. T1 now misses only the size-capped workspace tree.
- 2026-11-24: full Python suite 1383 passing; frontend 222 tests passing.
