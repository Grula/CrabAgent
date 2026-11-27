Several bugs in `boltons.ioutils` (this workspace is the boltons library). Each should behave like the standard `io` streams:

1. `truncate(n)` on a spooled stream (`SpooledBytesIO` / `SpooledStringIO`, before and after `rollover()`) moves the stream position; `truncate` must never move it.
2. `SpooledBytesIO.readline(0)` returns a whole line instead of an empty result.
3. Reading `SpooledStringIO.len` changes the current position (e.g. after `seek(1)`, `len` then `tell()` no longer gives 1). It must leave the position alone, also after `rollover()`.
4. `SpooledStringIO.seek(-2, os.SEEK_END)` lands in the wrong place when the text has non-ASCII characters (it should return the character position counted from the end, like `io.StringIO`), and seeking to before the start must raise `ValueError` without moving the position.
5. After `readline()` and then `rollover()`, a `SpooledStringIO` with non-ASCII text reports a wrong `tell()` and reads from the wrong place.
6. `MultiFileReader.read(0)` reads everything instead of returning an empty result; negative sizes should read to the end. After reading to the end, `seek(0)` should start over from the first file (it currently continues from a later one).

Please fix these with tests and keep the existing tests passing.
