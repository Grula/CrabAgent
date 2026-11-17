---
name: go-build-debug
description: Build, test and debug Go - go build/vet/test, focused and race-detected test runs, module hygiene, goroutine dumps for hangs, profiling, and the Go pitfalls behind most wrong results. Use for Go build failures, failing or flaky tests, panics, deadlocks or slow code.
---

# Go: Build, Test, Debug

Commands run through `bash` are not interactive: skip `dlv` sessions and use the
recipes below, which run to completion.

## 1. Know the module
- `go version`, and the `go` and `toolchain` lines in `go.mod`. Language behaviour
  depends on the `go` line; for example, loop variables are per-iteration only from
  `go 1.22`.
- If `vendor/` exists, the build uses it. Don't bypass it.
- Don't run `go get -u` or `go mod tidy` unless the task needs a dependency change:
  both rewrite `go.mod` and `go.sum`.
- Generated files ("Code generated ... DO NOT EDIT"): change their source, then run
  `go generate ./...`.
- Docs for the installed version: `go doc <pkg>.<Symbol>`, or pkg.go.dev (see
  `docs-lookup`).

## 2. Build and vet
```bash
go build ./...
go vet ./...
gofmt -l .        # lists unformatted files; fix with gofmt -w <file>
```
Run `golangci-lint run` only if the project has its config.

## 3. Tests
```bash
go test ./path/to/pkg -run '^TestName$' -count=1 -v      # one test, no cache
go test ./... -count=1                                    # everything
go test ./path/to/pkg -race -count=1                      # data races
go test ./path/to/pkg -run '^TestName$' -count=200        # flaky test
go test ./path/to/pkg -run '^$' -bench . -benchmem        # benchmarks only
```
- `-count=1` turns off the test cache, so the output reflects your change.
- Subtests: `-run '^TestName$/^case name$'`.
- Follow the package's style: table-driven tests, `t.Run`, `t.Helper()`, files under
  `testdata/`. Some golden files are updated with a flag such as `-update`; check the
  test code before using one.

## 4. Panics, hangs and deadlocks
- A panic prints the goroutine and stack. Read your frame, not the runtime's.
- A hanging test: `go test -timeout 60s ...`. On timeout Go panics and prints every
  goroutine's stack; look for the ones blocked on a channel, a mutex or `Wait`.
- A hanging program: `kill -QUIT <pid>` dumps all goroutines and exits. Add
  `GOTRACEBACK=all` for more detail.
- "all goroutines are asleep - deadlock!": a send or receive with no partner, or a
  `WaitGroup` that never reaches zero.

## 5. Pitfalls behind most wrong results
- Writing to a nil map panics; reading from one doesn't.
- An interface holding a typed nil pointer is not `== nil`.
- `append` may share the backing array: two slices from one base can overwrite each
  other.
- `err :=` inside a block shadows the outer `err`, which stays nil.
- Map iteration order is random; don't depend on it in code or tests.
- Goroutines that never finish: unbuffered channels nobody reads, a missing
  `ctx.Done()` case.
- `defer` inside a loop runs at function exit, not per iteration.
- Errors: wrap with `%w`, check with `errors.Is` and `errors.As`, not string matching.
- Time in tests: inject a clock or a deadline instead of `time.Sleep`.

## 6. Performance
```bash
go test ./path/to/pkg -run '^$' -bench 'BenchmarkX' -cpuprofile cpu.out -memprofile mem.out
go tool pprof -top cpu.out
```
Measure before and after. Change one thing at a time.

## Fixing
Fix the cause, keep the diff minimal, run the failing test with `-count=1`, then the
package's tests, and `-race` if goroutines are involved (see `root-cause-debugging`
for the method). If your role or brief says not to change code (a tester or
reviewer), stop before fixing and report the cause with its evidence: the failing
command, the panic or goroutine dump.
