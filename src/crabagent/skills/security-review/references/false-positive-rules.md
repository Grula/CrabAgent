# False-positive rules for security review

A security finding must describe a concrete attack: who the attacker is, what input they
control, how it reaches the vulnerable code, and what they gain. Drop any candidate that
matches the rules below unless the attack path is unusually clear.

## Do not report

1. **Availability-only issues** - denial of service, rate limiting, resource or memory
   exhaustion, slow regexes (ReDoS), large payloads.
2. **Missing hardening** - absent security headers, missing audit logs, lack of defense in
   depth, "could also validate X" without a demonstrated exploit.
3. **Dependency versions** - outdated or vulnerable third-party packages are tracked by
   separate tooling.
4. **Test, fixture, and example code** - files used only for tests or demos.
5. **Documentation** - markdown and other docs are not executable.
6. **Trusted inputs** - environment variables, CLI flags of a locally run tool, config files
   owned by the operator, and values that only an administrator can set.
7. **Client-side checks** - missing auth or validation in browser/mobile client code; the
   server is responsible. Report only when the *server* trusts the client's decision.
8. **Framework-escaped output** - React, Vue, Angular, Jinja2 with autoescape, and similar
   escape by default. Report XSS only when the code explicitly bypasses escaping.
9. **Memory-safety claims in memory-safe languages** - buffer overflows or use-after-free in
   Python, JavaScript, Go, Java, C#, or safe Rust.
10. **Theoretical races and timing attacks** - report a race only with a concrete,
    practical interleaving that yields a security impact.
11. **Log content** - writing unsanitized user input to logs (log injection) is not a
    finding. Logging secrets, passwords, tokens, or personal data **is**.
12. **Regex injection** - user input in a regular expression is not a vulnerability by itself.
13. **Path-only SSRF** - attacker controls only the path or query of a request to a fixed
    host. SSRF requires control of host or scheme.
14. **Resource leaks** - unclosed files, sockets, or handles.
15. **Secrets at rest** - credentials stored in a protected location on disk.
16. **Unguessable identifiers** - UUIDv4 and similar random IDs do not need extra validation.
17. **Low-impact web issues** - tabnabbing, XS-leaks, prototype pollution, open redirects -
    unless the impact is concrete and high.
18. **CI workflows and shell scripts** - only report injection when untrusted input (e.g. a
    pull-request title from a fork) demonstrably reaches the command.
19. **Notebooks** - only with a specific path for untrusted input to trigger execution.
20. **Prompt content** - untrusted text inside a model prompt is not a finding on its own.
    It becomes one when the model's output can trigger privileged actions (shell, file
    writes, outbound requests, payments) without a permission or confirmation step.

## Keep when

- The vulnerable code is reachable from an entry point an attacker can call.
- The attacker's input is not neutralized earlier (validation, parameterization, escaping,
  allowlists, auth checks).
- The impact is meaningful: code execution, data access across users or tenants,
  privilege escalation, account takeover, or exposure of secrets/personal data.
- The issue was introduced or newly exposed by this change.

## Confidence scale

- **9-10** - attack path traced end to end through the code.
- **8** - well-known exploitable pattern, input very likely attacker-controlled.
- **7 and below** - depends on assumptions you could not confirm. Do not report.
