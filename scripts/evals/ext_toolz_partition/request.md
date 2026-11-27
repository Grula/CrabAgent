`toolz.partition_all` silently returns wrong data when the sequence it is given reports a wrong length. This workspace is the toolz library.

For example, a `list` subclass whose `__len__` is off by one (too long or too short) makes `list(partition_all(5, seq))` return a last chunk with missing or padding items instead of the real elements. Since an object with a wrong `__len__` is a bug in that object, `partition_all` should detect it and raise `LookupError` instead of returning bad data. Sequences with a correct length, and plain iterators, must keep working exactly as they do now.

Please fix this with a test and keep the existing tests passing.
