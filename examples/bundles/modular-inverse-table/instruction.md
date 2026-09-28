# Modular inverse table

`/app/queries.txt` describes a batch of modular-inverse queries:

- The first line is `modulus M`, where `M` is an integer greater than 1.
- Every following line holds one integer `a`. It may be zero, negative or much
  larger than `M`.

Write `/app/inverses.txt` with exactly one line per query, in input order:

- If `gcd(a, M) = 1`, the line is the unique integer `x` with `0 <= x < M` and
  `(a * x) mod M = 1`, written in base 10 with no sign and no leading zeros.
- Otherwise the line is the word `none`.

Reduce `a` into the range `0 .. M-1` first, so `-1` behaves exactly like `M - 1`.
Use `\n` line endings and end the file with a single trailing newline. Do not
modify `/app/queries.txt`.
