# Creational Patterns

Use a creational pattern when *how* an object is made varies independently of the code that uses it. If the constructor is stable and called in one place, keep the constructor.

## Factory Method
- **Intent:** Move the choice of concrete product behind one overridable creation method, so callers depend on a product interface instead of a constructor.
- **Use when:**
  - the concrete type or its dependencies are only known at runtime or from configuration;
  - you ship a library or framework and want users to substitute a component without editing it;
  - construction should reuse a pool or cache rather than always allocate.
- **Don't use / cost:** one creator subclass per product plus an extra layer; with one product and one call site, a constructor or a plain `make_x()` is clearer. Over-engineered sign: a factory class whose only job is to return one known concrete type.
- **Python / TypeScript form:** a module function or `@classmethod` returning a `Protocol`; a `dict[str, Callable]` for a fixed set; in TS, a function returning an interface, or an abstract creator with `protected abstract create(): Product`.
- **Related:** evolve to Abstract Factory when variants come in families; it is a specialization of Template Method.

## Abstract Factory
- **Intent:** Create a matching family of products through one factory interface, so a caller cannot accidentally mix variants.
- **Use when:**
  - code must support several product families or variants without naming their concrete classes;
  - products must be compatible with each other (same theme, platform or vendor);
  - a class is accumulating several factory methods and losing its main responsibility.
- **Don't use / cost:** many interfaces and classes, and every new product type changes every factory; with one variant, or one product per family, Factory Method is enough. Over-engineered sign: a factory interface with a single `create_*` method or a single implementation.
- **Python / TypeScript form:** a `Protocol` with several `create_*` methods implemented by one small class per variant, chosen once at startup and injected; in TS, `interface GUIFactory { createButton(): Button; ... }`.
- **Related:** usually built from Factory Methods; differs from Builder, which assembles one complex product step by step.

## Builder
- **Intent:** Assemble a complex object through explicit steps, so the same process can produce different representations and skip irrelevant options.
- **Use when:**
  - constructors have grown a long tail of optional parameters or overloads;
  - the same steps must yield different representations (for example, an object and its manual);
  - you are building a composite tree or an object whose parts are assembled recursively.
- **Don't use / cost:** several new classes and a director that must be kept in sync; only worthwhile for genuinely complex products. Over-engineered sign: a builder for a three-field dataclass, or a director with one fixed recipe.
- **Python / TypeScript form:** keyword arguments and `@dataclass` already cover many cases; when order or reuse matters, use a fluent builder returning `self` with a final `build()`, or a typed options object in TS.
- **Related:** differs from Abstract Factory (step-by-step construction of one product vs immediate creation of a family); useful for Composite trees.

## Prototype
- **Intent:** Copy an existing configured object through a clone operation, so the caller does not need to know or depend on its concrete class.
- **Use when:**
  - objects arrive through an interface and their concrete classes are unknown or third-party;
  - many subclasses exist only to produce preset configurations;
  - you need to copy a complex configured structure (a tree, a command, a document) cheaply.
- **Don't use / cost:** shallow vs deep copy and circular references are easy to get wrong; the clone can silently share mutable state. Over-engineered sign: a `clone()` method on a simple immutable value, or when calling the constructor with the same arguments is shorter.
- **Python / TypeScript form:** `copy.copy`/`copy.deepcopy`, `dataclasses.replace`, or `__copy__`/`__deepcopy__` for control; in TS, a `clone()` method (or `structuredClone` where available) rather than hand-copying fields at each call site.
- **Related:** often pays off with Composite and Decorator, where cloning a built structure beats rebuilding it; can be a simpler alternative to Memento when the state is self-contained.

## Singleton
- **Intent:** Guarantee one instance of a class and provide a single access point to it.
- **Use when:**
  - one shared resource must be coordinated across the process (a connection pool, a cache, configuration);
  - you need stricter control than a mutable global variable provides.
  Lazy creation is a side benefit, not a reason: `functools.cache` on a getter gives it without a class.
- **Don't use / cost:** it violates SRP, hides dependencies, complicates tests and needs explicit thread-safety; it often becomes a service locator. Over-engineered sign: a singleton around a stateless helper, or a second global used only to avoid passing a dependency.
- **Python / TypeScript form:** a module-level instance is the idiomatic Python singleton because the module is imported once; in TS, a module-level `const`/object, or a class with a private constructor and `static get instance()`. Prefer injecting the shared object at the composition root.
- **Related:** a Facade is often made a Singleton; Flyweight is not a Singleton because it has many immutable shared instances.
