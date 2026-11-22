# Design Principles

Treat these as diagnostics, not a scorecard. A violation is a question about cost: name the edit it makes expensive or the bug it invites, or drop it.

## Encapsulate What Varies
- **Idea:** Put the part that changes behind one named seam so a change lands in one place and the rest of the code does not need to know about it.
- **Flag it when:** one new rule edits several call sites; the same pricing, format, retry or permission logic is copied across modules; a diff adds a branch to three unrelated functions.
- **Fix:** extract the varying decision into a function, class or module named after that decision, and call it from one place; pass the varying value in instead of recomputing it.
- **Skip it when:** there is one variant and one caller, or the new layer is harder to follow than the duplicated lines it replaces; a plain function is enough, so do not build a class hierarchy for it.

## Program to an Interface, not an Implementation
- **Idea:** Make the caller depend on the few operations it actually needs, expressed at its own boundary, not on the concrete class that provides them today.
- **Flag it when:** domain code imports a vendor SDK, ORM model or concrete client; swapping a provider or faking one in a test edits many files; constructors name infrastructure types.
- **Fix:** declare a narrow Protocol or interface at the caller in the caller's terms, adapt the concrete object to it, and inject it; keep construction at the composition edge.
- **Skip it when:** there is one implementation and no real second one now; the abstraction is a one-to-one copy of the library API; the dependency is a plain value object or a stable stdlib call. A single test double is not enough reason to add an interface.

## Favor Composition over Inheritance
- **Idea:** Assemble behavior by holding and delegating to collaborators so each piece can change independently; inherit only for a genuine, stable is-a relationship.
- **Flag it when:** a subclass overrides a method to disable it or raise; `super()` calls must happen in a fixed order; adding one new axis of variation doubles the class count; the same override is copy-pasted across siblings.
- **Fix:** move the varying behavior into a collaborator and pass it in; keep a base class only for the invariant skeleton, and put the variation behind a small interface.
- **Skip it when:** the hierarchy is a real taxonomy with a stable contract and one small override; replacing a three-line subclass with a collaborator, a Protocol and wiring makes the code larger and less direct.

## Single Responsibility Principle
- **Idea:** Give each module one actor or concern whose changes can force an edit, so unrelated changes do not collide in the same file.
- **Flag it when:** a diff adds an unrelated concern to an existing class (HTTP parsing next to persistence next to formatting); tests for one behavior need fixtures from another; unrelated tasks keep conflicting in the same file.
- **Fix:** split along the reasons to change, move each concern into its own module, and delegate from the original.
- **Skip it when:** the code is small and cohesive (a few hundred lines with one job); splitting would produce anemic classes and more jumps between files than insight. Count actors, not methods.

## Open/Closed Principle
- **Idea:** Make the common case extensible by adding code at a seam, not by editing code that is already tested and working.
- **Flag it when:** every new variant edits the same `if`/`elif` or `switch`; adding a format, payment provider or export type touches an existing tested function; the diff adds parallel branches in several places.
- **Fix:** place one seam at the variation point (a function table, a small Protocol, a subclass hook) and add a new implementation instead of another branch.
- **Skip it when:** you are fixing a bug (fix it, do not subclass around it); the set of variants is small and stable; the extension point costs more than the edits it avoids. Do not build a plugin system for one known variant.

## Liskov Substitution Principle
- **Idea:** A subtype must be usable anywhere the base type is expected, without surprising the caller with narrower inputs, broader outputs or new failures.
- **Flag it when:** an override raises `NotImplementedError` or rejects inputs the base accepts; it strengthens validation, weakens a postcondition, returns a wider type or throws a new exception; callers add `isinstance` checks to avoid a subtype.
- **Fix:** redesign the hierarchy so the restricted type is the base (a read-only document above a writable one), split the interface, or use composition; keep the base contract honest.
- **Skip it when:** nothing actually consumes the base type polymorphically; the conflict is theoretical; the hierarchy is private and has one caller you can update. In dynamic code, check substitution only where base-typed values really flow.

## Interface Segregation Principle
- **Idea:** Keep interfaces narrow and role-specific so no client or implementation is forced to depend on operations it does not use.
- **Flag it when:** implementations contain `pass` or `raise NotImplementedError` stubs; a test fake implements a dozen methods to satisfy one; adding a method to a shared Protocol forces edits in every implementer.
- **Fix:** split the interface by client role, accept the smallest Protocol each caller needs, and compose interfaces on the implementation side.
- **Skip it when:** there is one implementer and one caller; every method on the interface is genuinely used; splitting would create many tiny interfaces with no independent consumers. In Python, `typing.Protocol` is cheap, but it still costs a definition and an import.

## Dependency Inversion Principle
- **Idea:** Make high-level policy and low-level detail both depend on an abstraction owned by the policy layer, so details can change without rewriting policy.
- **Flag it when:** a service or domain module imports a concrete database, HTTP client or SDK; changing a provider or adding a test double edits the policy module; infrastructure types appear in domain signatures.
- **Fix:** define a port in the domain in domain terms, implement it in the infrastructure layer, and inject the implementation at the composition root.
- **Skip it when:** policy and detail are the same small script; the dependency is stable and already abstract enough; the wrapper would only forward calls. Do not invert a dependency that does not exist.

## Flagging rule
Name the concrete future edit this makes cheaper or the concrete failure it prevents. If you cannot, it is a preference, not a review finding.
