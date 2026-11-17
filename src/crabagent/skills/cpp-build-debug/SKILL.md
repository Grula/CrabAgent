---
name: cpp-build-debug
description: Build, test and debug C and C++ - the project's build system (CMake, Make, Meson), compile and linker errors, sanitizers, gdb in batch mode, core dumps, undefined behaviour. Use for C/C++ build failures, crashes, wrong results, memory errors or flaky tests.
---

# C and C++: Build, Test, Debug

Commands run through `bash` are not interactive: a plain `gdb` session or a program
waiting for input will hang. Every recipe here runs to completion.

## 1. Use the project's build, not your own
- Find it: `CMakeLists.txt` (and `CMakePresets.json`), `meson.build`, `Makefile`,
  `BUILD`/`WORKSPACE` (Bazel). The `run-app` skill covers finding the recipe.
- Reuse the existing build directory and options. For extra builds (sanitizers,
  debug), use a separate directory such as `build-asan/`, so the project's build stays
  as it was.
- Know the compiler and the language standard before relying on a feature:
  `c++ --version`, and `-std=` in `compile_commands.json` or `CMAKE_CXX_STANDARD`.
  Check "since C++XX" on cppreference (see `docs-lookup`).

CMake, the common case:
```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Debug -DCMAKE_EXPORT_COMPILE_COMMANDS=ON   # or: cmake --preset <name>
cmake --build build -j --target <target>          # build only what you changed
ctest --test-dir build --output-on-failure -R '<test regex>'
```

## 2. Compile errors
- Fix the **first** error and rebuild; later errors are often its echoes.
- Template errors: find your own line in the chain ("required from here" in GCC,
  "in instantiation of" in Clang); the deepest frames are library internals.
- Don't silence a warning you introduced, and don't change project-wide warning
  flags. Fix the code.

## 3. Linker errors
| Message | Usual cause |
|---|---|
| `undefined reference to X` | the source isn't in the target; the library isn't linked (`target_link_libraries`); static libraries in the wrong order; C code called from C++ without `extern "C"`; a template defined in a `.cpp` |
| `undefined reference to vtable for X` | the first non-inline virtual function of `X` has no definition |
| `multiple definition of X` | a non-inline function or variable defined in a header: make it `inline` or move it to one `.cpp` |

Look up symbols with `nm -C <lib or .o> | grep <name>`; demangle with `c++filt`.

## 4. Crashes and memory errors
Rebuild with sanitizers in a separate directory, then run the failing test:
```bash
cmake -S . -B build-asan -DCMAKE_BUILD_TYPE=Debug \
  -DCMAKE_CXX_FLAGS="-fsanitize=address,undefined -fno-omit-frame-pointer"
cmake --build build-asan -j --target <test target>
UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1 ./build-asan/<test binary>
```
- Data races: a separate `-fsanitize=thread` build (it can't be combined with ASan).
- No sanitizers available: `valgrind --leak-check=full --error-exitcode=1 ./prog`.
  Don't run valgrind on a sanitizer build.
- A backtrace without a debugger session:
  `gdb -batch -ex run -ex bt -ex "thread apply all bt" --args ./prog <args>`
- A core dump: `ulimit -c unlimited` before running, then `gdb -batch -ex bt ./prog core`
  (with systemd: `coredumpctl info` / `coredumpctl debug`).
- A hang: `timeout 60 ./prog`, or attach once:
  `gdb -batch -p <pid> -ex "thread apply all bt"`.

## 5. Undefined behaviour to suspect
Wrong results that change with the optimisation level, or that only show up in
release builds, usually mean undefined behaviour:
- use after free, dangling references, iterators invalidated by `push_back` or `erase`
- `std::string_view` or `std::span` outliving their buffer
- signed integer overflow, uninitialised variables, out-of-bounds indexing
- data races, and the static initialisation order across translation units
- ODR violations: one name defined differently in two places

The sanitizer build in section 4 catches most of these.

## 6. Tests
- Run one test first: GoogleTest `--gtest_filter='Suite.Name'`, Catch2 `"name"` or
  `"[tag]"`, doctest `-tc="name"`, CTest `-R`.
- Flaky tests: `--gtest_repeat=200 --gtest_shuffle` (or a loop) under the TSan build.
- If the project has `.clang-format`, format only the lines you changed
  (`git clang-format`); run `clang-tidy` only if the project already does.

## Fixing
Fix the cause, keep the diff minimal, rebuild the target and rerun the failing test.
Then run that target's tests (see `root-cause-debugging` for the method). If your role
or brief says not to change code (a tester or reviewer), stop before fixing and report
the cause with its evidence: the sanitizer report, the backtrace, the failing command.
