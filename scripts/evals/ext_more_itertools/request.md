`more_itertools.numeric_range` breaks for very large ranges. This workspace is the more-itertools library.

With integer ranges longer than `sys.maxsize`:

- comparing two of them raises `OverflowError` instead of answering: `numeric_range(0, 10**30) == numeric_range(0, 10**30)` should be `True`, and ranges with the same elements should compare equal the way built-in `range` objects do (`numeric_range(0, 10**30, 2) == numeric_range(0, 10**30 - 1, 2)` is `True`, a different start, stop or step that changes the elements is not equal);
- hashing one raises too, so they can't be used as dict keys or in sets. `hash(numeric_range(*args))` should equal `hash(range(*args))` for the same integer arguments, as it already does for small ranges.

`len()` of such a range should keep raising `OverflowError`, like `range` does. Please fix this with a test, and keep the existing tests passing.
