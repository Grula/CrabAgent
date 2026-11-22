# Structural Patterns

Use a structural pattern when the *wiring* between objects is the recurring problem. If one class simply needs one more method, add the method; do not add a layer.

## Adapter
- **Intent:** Wrap an object behind the interface the caller expects and translate calls and data to the wrapped object's API.
- **Use when:**
  - a useful class, legacy module or third-party library has an incompatible interface;
  - several existing subclasses need a common capability that cannot be added to their superclass;
  - you want to confine a vendor or legacy API to one boundary.
- **Don't use / cost:** adds interfaces and classes; when you own the service, changing its interface is usually simpler. Over-engineered sign: an adapter that forwards one-to-one with no translation, or an adapter around a class you just wrote.
- **Python / TypeScript form:** a small class holding the service and implementing a `Protocol`; a function wrapper is often enough in Python; in TS, `class XAdapter implements Target { constructor(private service: Service) {} }`.
- **Related:** Adapter changes the interface, Proxy keeps it, Decorator enhances it; Bridge is designed up front, Adapter is usually retrofitted.

## Bridge
- **Intent:** Split a class that varies along two independent dimensions into an abstraction hierarchy and an implementation hierarchy, connected by composition.
- **Use when:**
  - a monolithic class has several variants of one capability (platforms, database drivers, renderers);
  - you need to extend it along two orthogonal dimensions without a combinatorial explosion of subclasses;
  - implementations must be swappable at runtime.
- **Don't use / cost:** more classes and indirection; applying it to a highly cohesive class just makes it harder to follow. Over-engineered sign: two hierarchies where only one implementation exists and no second is planned, or a "platform" layer wrapping a single library.
- **Python / TypeScript form:** an abstraction class holding an implementation `Protocol` injected through the constructor; in TS, `class RemoteControl { constructor(protected device: Device) {} }`. The shape resembles Strategy; the intent differs.
- **Related:** Bridge is designed up front while Adapter is retrofitted; Bridge, State and Strategy share a structure but solve different problems.

## Composite
- **Intent:** Treat leaves and containers through one interface so a tree can be traversed and operated on uniformly.
- **Use when:**
  - the domain is naturally a tree (files, UI elements, org charts, expression ASTs);
  - clients should treat a single item and a group the same way;
  - an operation should recurse over the whole structure.
- **Don't use / cost:** forces a common interface that can become too generic; adding child management to the component interface can violate ISP. Over-engineered sign: a container that can only hold one leaf type, or `add`/`remove` methods that leaves never need.
- **Python / TypeScript form:** a `Protocol` with the shared operation, leaf and container classes, and a `list[Component]` in the container; a plain list plus a recursive function often suffices in Python; a TS discriminated union can model the tree without classes.
- **Related:** Builder can construct Composite trees, Iterator and Visitor operate on them; Decorator is like a Composite with one child but adds behavior instead of summing results.

## Decorator
- **Intent:** Add responsibilities to an object at runtime by wrapping it in another object with the same interface.
- **Use when:**
  - behavior must be added or removed per object at runtime (logging, retry, caching, auth, compression);
  - inheritance would create a subclass for every combination of features;
  - the class is final or third-party and cannot be extended.
- **Don't use / cost:** hard to remove one wrapper from a stack, behavior can depend on wrapper order, and assembly code gets ugly; debugging through layers is harder. Over-engineered sign: a decorator for behavior that is always applied, or a single decorator that is never composed.
- **Python / TypeScript form:** `@decorator` is the idiomatic Python form for functions; for objects, a wrapper class implementing the same `Protocol` and holding the inner object; in TS, a class implementing the interface with the inner object injected. Context managers handle setup/teardown, not behavior wrapping.
- **Related:** Adapter changes the interface and Proxy controls access, while Decorator enhances behavior; Chain of Responsibility also chains calls but handlers can stop the request and act independently.

## Facade
- **Intent:** Provide one small, stable entry point over a complex subsystem, hiding its objects and the order in which they must be called.
- **Use when:**
  - clients would otherwise initialize many subsystem objects and call them in a specific order;
  - you want a clear layer boundary between subsystems;
  - you need to isolate a third-party framework behind a small surface.
- **Don't use / cost:** can grow into a god object, hides useful capabilities and adds a layer to maintain. Over-engineered sign: a facade that forwards a single call, or one over a subsystem you own that already has a clean API.
- **Python / TypeScript form:** a module with a few functions is the usual Python facade; in TS, a class with one `convert()` or `run()` method. Keep it thin: if it only forwards, delete it.
- **Related:** Facade defines a new simplified interface while Adapter makes an existing one usable; Facade simplifies one-way access while Mediator centralizes two-way communication.

## Flyweight
- **Intent:** Share immutable intrinsic state across many objects and pass the varying extrinsic state in from outside, to cut memory use.
- **Use when:**
  - a huge number of similar objects strains available RAM;
  - most of their state is duplicated and can be shared;
  - the shared part can be immutable while the varying part is passed to methods.
- **Don't use / cost:** trades RAM for CPU and adds real complexity; never apply it without evidence of a memory problem. Over-engineered sign: a flyweight cache for a few dozen objects, or a pool added before profiling shows pressure.
- **Python / TypeScript form:** a `dict` cache keyed by intrinsic state, or `functools.cache` for a pure constructor; in TS, a `Map<string, Flyweight>` and a readonly record for the shared data.
- **Related:** shared leaf nodes of a Composite tree are a natural fit; Flyweight is not a Singleton because it has many immutable instances.

## Proxy
- **Intent:** Stand in for another object with the same interface and control access to it: lazy creation, authorization, caching, logging or remote calls.
- **Use when:**
  - a heavyweight service should be created only when needed;
  - requests must be checked, logged or cached before reaching the service;
  - the service is remote, unavailable or expensive to hold open.
- **Don't use / cost:** more classes and indirection, possible added latency, and failures can be hidden behind the stand-in. Over-engineered sign: a proxy that only forwards, or one around a cheap local object with no access policy.
- **Python / TypeScript form:** a class implementing the same `Protocol`, holding the real service and creating it on first use; `__getattr__` can proxy dynamically, but an explicit class is clearer; in TS, `class CachedClient implements Client` or the built-in `Proxy` for interception.
- **Related:** Proxy controls the service lifecycle while Decorator adds behavior and its stack is client-controlled; Proxy keeps the same interface while Adapter changes it.
