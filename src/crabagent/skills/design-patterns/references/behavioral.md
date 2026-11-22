# Behavioral Patterns

Use a behavioral pattern when the *interaction* is the recurring source of edits: who knows about whom, who triggers what, or how a request travels. If one direct call is enough, keep the direct call.

## Chain of Responsibility
- **Intent:** Pass a request along a chain of handlers until one handles it or the chain ends.
- **Use when:**
  - requests can be handled in several ways and the exact handlers or their order are not known up front;
  - several checks or processing steps must run in a specific order (auth, validation, rate limiting, caching);
  - the set of handlers or their order must change at runtime.
- **Don't use / cost:** some requests can end up unhandled, and the flow is harder to trace than a direct call. Over-engineered sign: a chain with one handler, or three always-run steps that a plain function composition would express more clearly.
- **Python / TypeScript form:** a list of callables or middleware functions is usually enough; web middleware stacks are this pattern; for classes, a `Protocol` with `handle` and an optional next handler. In TS, an array of middleware functions.
- **Related:** Chain of Responsibility differs from Decorator because handlers can stop the request and act independently; it often pairs with Composite when a leaf forwards a request to its parent.

## Command
- **Intent:** Turn a request into an object holding its receiver and parameters, so it can be passed, queued, logged, retried or undone.
- **Use when:**
  - operations must be parameterized, queued, scheduled or executed remotely;
  - undo/redo or a command history is required;
  - a sender such as a UI control should not know the receiver or the operation's details.
- **Don't use / cost:** adds a layer and many small classes between sender and receiver. Over-engineered sign: a command class around one function call when a closure or callback would do, or when there is no queue, history or undo requirement.
- **Python / TypeScript form:** a callable or `functools.partial` is the lightweight command; use a `Protocol` with `execute()` and explicit fields when the command must be serialized or inspected. In TS, an interface `{ execute(): void }`, a function, or a discriminated union of command objects.
- **Related:** pair Command with Memento for undo, where the command performs the operation and the memento stores the prior state; Command differs from Strategy because it packages an operation, not an alternative way to do the same task.

## Iterator
- **Intent:** Move traversal out of the collection into a separate object, so clients walk elements without knowing the underlying structure.
- **Use when:**
  - the collection has a complex structure and clients need a simple traversal API;
  - code must walk different, or not-yet-known, data structures through one interface;
  - the same collection needs several traversal orders or independent, parallel iterations;
  - traversal code is duplicated across the app.
- **Don't use / cost:** overkill for a simple list; adds an object and indirection and can be slower than direct access. Over-engineered sign: a custom iterator class around a plain list when a generator or `for` loop already says everything.
- **Python / TypeScript form:** Python's iterator protocol and generators are the default: implement `__iter__` with `yield`, or use `itertools` to compose traversals. In TS, a generator function or `Symbol.iterator`; arrays already iterate.
- **Related:** use iterators to traverse Composite trees; Factory Method lets collection variants return their own compatible iterators; Visitor often uses an iterator to walk the elements it visits.

## Mediator
- **Intent:** Centralize communication between many components so they reference the mediator instead of each other.
- **Use when:**
  - many components are tightly coupled and a change to one ripples through others;
  - a component cannot be reused because it references its colleagues;
  - you keep creating subclasses only to vary how components interact.
- **Don't use / cost:** the mediator can grow into a god object and becomes a new single point of complexity. Over-engineered sign: a mediator between two components that could call each other directly, or a mediator that only forwards messages.
- **Python / TypeScript form:** a coordinating class holding references to components, with components calling `mediator.notify(self, event)`; in TS, a `Mediator` interface plus a `mediator` field on each component. A small event bus can serve when the coordination is simple.
- **Related:** Mediator centralizes two-way coordination while Observer provides dynamic one-way subscriptions; a mediator is often implemented with Observer internally. Facade simplifies access to a subsystem without centralizing communication.

## Memento
- **Intent:** Capture and restore an object's state without exposing its internals to the caller.
- **Use when:**
  - undo, rollback or transaction-like restoration is needed;
  - snapshots must not break encapsulation;
  - the object itself can produce and consume the snapshot.
- **Don't use / cost:** snapshots can consume a lot of memory, and the caretaker must discard obsolete ones; dynamic languages cannot enforce that the snapshot stays untouched. Over-engineered sign: a memento for a tiny value that can be reconstructed from its inputs, or when a fresh object or `copy.deepcopy` already solves it.
- **Python / TypeScript form:** an immutable dataclass or tuple snapshot, `copy.deepcopy` for a quick copy, or `__getstate__`/`__setstate__` when the object controls its own serialization; in TS, a `readonly` snapshot type plus `restore(snapshot)`. Keep the snapshot opaque to the caretaker.
- **Related:** Command and Memento together implement undo; Prototype can be a simpler alternative when the state is self-contained; Iterator can capture and restore an iteration position.

## Observer
- **Intent:** Let subscribers register for events from a publisher so one change notifies many interested objects without coupling them.
- **Use when:**
  - changes to one object require updating others, and the set of observers is unknown or changes dynamically;
  - components must subscribe and unsubscribe at runtime;
  - an event should reach many independent listeners.
- **Don't use / cost:** notification order is unspecified, subscriptions can leak, and event chains are harder to debug than direct calls. Over-engineered sign: an event system for one publisher and one subscriber, or observers used where returning a value would do.
- **Python / TypeScript form:** a list of callbacks, a small `EventBus`, or a signal library; in TS, `addEventListener` or a `subscribe` that returns an unsubscribe function, or a typed emitter. Always give callers a way to unsubscribe.
- **Related:** Observer differs from Mediator (distributed one-way subscriptions vs centralized coordination); Chain of Responsibility, Command, Mediator and Observer are four different ways to connect senders and receivers.

## State
- **Intent:** Let an object change behavior when its internal state changes by delegating state-specific work to state objects.
- **Use when:**
  - behavior depends on a state or mode, the number of states or transitions is large, and state-specific code changes often;
  - a class is polluted with conditionals on a state field;
  - similar states duplicate transition code.
- **Don't use / cost:** many classes for a small or rarely changing state machine, and state objects may need access to context internals. Over-engineered sign: a state class per value of a two-state boolean, or a state machine that never transitions at runtime.
- **Python / TypeScript form:** a `Protocol` or ABC with the state methods and a context holding the active state; in Python, a `dict` of functions or an `Enum` plus `match` is often enough; in TS, a discriminated union plus a reducer can replace the classes.
- **Related:** State and Strategy share a structure, but states know each other and trigger transitions while strategies do not; State can be seen as an extension of Strategy.

## Strategy
- **Intent:** Make algorithms interchangeable behind one interface so the context can switch behavior without conditionals.
- **Use when:**
  - several variants of an algorithm must be selected or swapped at runtime;
  - similar classes differ only in the way they execute one behavior;
  - a large conditional selects between variants of the same algorithm.
- **Don't use / cost:** only a couple of stable algorithms means needless classes and indirection, and clients must understand the differences to choose one. Over-engineered sign: a strategy interface with one implementation, or replacing a two-branch `if` with classes plus a factory.
- **Python / TypeScript form:** pass a function or callable, which is the default in Python; use a `Protocol` only when a strategy carries state or needs several methods, or `functools.partial` to bind arguments. In TS, a function type, an interface with `execute`, or a discriminated union.
- **Related:** Strategy differs from State because strategies are independent and do not transition; it differs from Command because Strategy describes alternative ways to do one task, while Command packages an operation as an object.

## Template Method
- **Intent:** Define the skeleton of an algorithm in a base class and let subclasses override specific steps without changing the order.
- **Use when:**
  - several classes contain almost identical algorithms with minor differences;
  - clients should extend only particular steps, not the whole algorithm or its structure;
  - the order of steps must stay fixed.
- **Don't use / cost:** inheritance is static, subclasses are constrained by the skeleton, and many steps make maintenance harder; a subclass that suppresses a step can violate LSP. Over-engineered sign: a base class with one subclass, or a template method whose steps are all trivial.
- **Python / TypeScript form:** an abstract base class with a concrete `run()` that calls `self.step()` hooks; in Python, passing functions as hooks or a `Protocol` often replaces it; in TS, an abstract class with a documented non-overridable `run()` and protected abstract steps.
- **Related:** Factory Method is a specialization of Template Method, and a factory method can be one step in a larger template; Strategy achieves similar variation through composition and can switch at runtime, while Template Method works at the class level.

## Visitor
- **Intent:** Move an operation over a set of element classes into a separate visitor, using double dispatch to select the right method.
- **Use when:**
  - an operation must run over a complex object structure containing many element types;
  - the operation is auxiliary and should not live in the element classes;
  - a behavior makes sense for only some classes in a hierarchy.
- **Don't use / cost:** every new element type forces an update to every visitor, visitors may need access to private element data, and elements gain an `accept` method. Over-engineered sign: a visitor hierarchy for one operation and one element type, or when a simple stable `match`/`isinstance` loop is clearer.
- **Python / TypeScript form:** `accept(visitor)` on elements plus `visit_x` methods on the visitor; `functools.singledispatch` can implement double dispatch without changing element classes; in TS, a discriminated union plus an exhaustive `switch` often replaces Visitor.
- **Related:** Visitor is a powerful form of Command over objects of different classes; it works with Composite and Iterator to traverse a structure and operate on each element.
