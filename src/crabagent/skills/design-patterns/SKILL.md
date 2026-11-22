---
name: design-patterns
description: Decide whether a change needs a design pattern or a new abstraction, and which one; spot over-engineering and missing seams. Use when planning a multi-file feature, briefing a shared interface, weighing architecture options, or reviewing a diff that adds or lacks an abstraction.
---

# Design Patterns

Decide whether an abstraction is earned. Do not teach the patterns; choose one, price it, or reject it.

## Default rule
Write the simplest code that works. Do not add an abstraction because a pattern exists or because a future variant might appear.
A pattern is earned only when one of these is true now:
- a second real variant exists in the codebase or the current request;
- the same change keeps landing in several places, and a seam would collapse them to one.
A hypothetical future need does not count. If you cannot name the concrete edit the abstraction makes cheaper, or the concrete bug it prevents, do not add it.
Prefer the smallest seam that works: direct code → a function → a dispatch table → a Protocol/class → a named pattern. Stop at the first one that passes the check.

## Symptom → pattern
| What you see | Consider first | Read |
|---|---|---|
| An `if`/`elif` or `switch` on a type or mode that keeps growing | Strategy; State if branches also change the object's state; a dict/table if dispatch is simple | `references/behavioral.md` |
| Wrapping a third-party API to fit our interface | Adapter; Facade if clients need one simple entry point | `references/structural.md` |
| Logging, retry, auth or caching around existing calls | Decorator; Proxy if access or lifecycle is the point; middleware or Chain of Responsibility for a pipeline | `references/structural.md`, `references/behavioral.md` |
| Many parts reacting to one event | Observer | `references/behavioral.md` |
| Undo, queuing or retrying operations | Command; add Memento for undo history | `references/behavioral.md` |
| Tree structures | Composite; Iterator and Visitor to traverse and operate | `references/structural.md`, `references/behavioral.md` |
| Building a complex object in steps | Builder | `references/creational.md` |
| Families of related objects that must match | Abstract Factory | `references/creational.md` |
| A concrete class chosen at runtime or by config | Factory Method | `references/creational.md` |
| One shared instance or resource | Singleton only after a module-level instance or dependency injection is rejected | `references/creational.md` |
| Traversing a collection without exposing its structure | Iterator; a generator first | `references/behavioral.md` |
| Components tangled in many-to-many calls | Mediator | `references/behavioral.md` |
| Two independent dimensions of variation | Bridge | `references/structural.md` |
| Many similar objects straining memory | Flyweight, only after measuring | `references/structural.md` |
| Lazy init, access control or caching around one service | Proxy | `references/structural.md` |
| Cloning configured objects without knowing their class | Prototype | `references/creational.md` |
| A fixed algorithm skeleton with varying steps | Template Method | `references/behavioral.md` |
| An operation over many unrelated element types | Visitor | `references/behavioral.md` |
| One of several handlers should process a request, in order | Chain of Responsibility | `references/behavioral.md` |

## For the architect
Brief the what, not the how: goal, files, pass/fail check, and what to leave alone. Do not name classes, methods, patterns or code in the brief. Name the seam in one sentence and state its blast radius.
- "New export formats go behind one exporter interface, so adding a format touches one new file."
- "Provider calls go through one client interface; the rest of the code does not import the vendor SDK."
- "A save publishes one event; interested modules subscribe; the saver does not call them."
- "One module owns retry policy; callers ask it to run the operation and do not write their own loops."

Good: "New export formats go behind one exporter interface, so adding a format touches one new file."
Bad: "Add an `ExportStrategy` protocol with `PdfExporter` and `CsvExporter` classes and a factory."
If one function in one file satisfies the brief, say that instead of inventing a layer.

Ask the user before adding a layer that is expensive to undo:
- a new service or module boundary, or a new public interface/Protocol;
- a new dependency, package or external service;
- an event bus, background queue or cache with invalidation;
- a plugin system, strategy registry or config switch with one current value;
- a schema or API change that other callers must follow.

## For the reviewer
Review the diff, not the pattern vocabulary.

Over-engineering signals:
- an interface or Protocol with one implementation and one consumer;
- a factory that builds exactly one thing;
- a pass-through wrapper, module or layer that adds no translation, policy or state;
- a class or file named after a pattern (`FooStrategy`, `BarFactory`) with a single variant;
- hooks, registries or config flags with no current user;
- an abstraction added "for testing" when the existing seam can take a fake;
- a wrapper stack where one function or one decorator would do.

Missing-seam signals:
- the same type or mode switch in three or more places;
- copy-pasted branches that differ by one line;
- one feature requiring edits across many files;
- callers importing a concrete vendor, database or HTTP client;
- tests that need the real external service because there is no seam;
- a new variant that repeats the same checklist of edits.

Worth flagging:
- a new switch or duplicate that will need the same edit again;
- a bypassed seam that forces callers back onto a concrete dependency;
- a public interface or schema change that makes later removal expensive.

Nitpicks, do not block:
- naming and file placement;
- local function-versus-class taste when both are small;
- a single-implementation interface that is a deliberate test seam for an external dependency;
- a thin wrapper that documents a real boundary.

Report as: problem, why now, smallest fix. If the diff is fine, say "no design issue" and stop.

## Language features that remove the pattern
- First-class functions: Strategy and Command are often a callback or `functools.partial`.
- Generators and `__iter__`: Iterator is built in; use `yield`, not an iterator class.
- Modules: Singleton is a module-level instance; Facade is a module with a few functions.
- `@decorator`: Decorator for functions before you write wrapper classes.
- Context managers: setup, teardown and resource cleanup, not a Proxy or Decorator hierarchy.
- `typing.Protocol` and duck typing: Adapter and Strategy seams without ABCs; do not add inheritance just to type a boundary.
- TS discriminated unions and exhaustive `switch`: State, Visitor, Strategy and Command often become one union plus a reducer.
- `dataclass`, keyword arguments and typed options objects: Builder only when construction is genuinely stepwise or recursive.
- `functools.cache`, a dict, `copy.deepcopy`, `dataclasses.replace`: Flyweight, Prototype and caching proxies in one line.

## Reading the references
Load only what the symptom table names, with `read` and a path relative to this folder.
- `references/principles.md`: deciding whether an abstraction is justified at all, or judging a SOLID/design-principle claim.
- `references/creational.md`: how objects are created, configured, cloned or shared.
- `references/structural.md`: wiring, wrapping, adapting, composing trees, controlling access.
- `references/behavioral.md`: interaction, dispatch, events, state, algorithms, traversal, operations over objects.
If a symptom spans two files, read the smaller one first and follow its Related line.
