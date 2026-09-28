# Integer linear system

`/app/system.json` holds a JSON object with two keys:

- `"A"`: an n x n matrix of integers, given as a list of n rows;
- `"b"`: a list of n integers.

The determinant of `A` is +1 or -1, so the system `A x = b` has exactly one
solution, and every entry of that solution is an integer.

Write `/app/x.txt` containing that solution: n lines, where line `i` holds
`x[i]` in base 10 (a leading `-` for negative values, no `+`, no leading zeros,
and `0` for zero). Use `\n` line endings and end the file with a single trailing
newline. Do not modify `/app/system.json`.
