"""An original synthetic paraphrase corpus for the near-duplicate tests.

Twelve distinct tasks (instruction plus a small Python solution), each with a
hand-written paraphrase of the instruction and a mechanically disguised copy
of the solution (identifiers renamed, comments and blank lines added). The
tests treat (task, its own paraphrase) as positive pairs and every pair of
different tasks as a negative pair.
"""

from __future__ import annotations

import builtins
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CorpusTask:
    key: str
    instruction: str
    paraphrase: str
    solution: str


TASKS = [
    CorpusTask(
        "csv-column-median",
        "Read the CSV file at /data/prices.csv and print the median of the price column. "
        "Rows with an empty price must be skipped. Print the result rounded to two decimals.",
        "Open /data/prices.csv, skip every row whose price is empty, and print the median "
        "of the price column rounded to two decimals.",
        "import csv, statistics\n"
        "def median_price(path):\n"
        "    with open(path) as handle:\n"
        "        values = [float(r['price']) for r in csv.DictReader(handle) if r['price']]\n"
        "    return round(statistics.median(values), 2)\n",
    ),
    CorpusTask(
        "log-error-counter",
        "Parse the server log in /var/log/app.log and count how many lines have level ERROR "
        "for each hour. Write the counts as JSON to /out/errors.json keyed by hour.",
        "For each hour, count the lines of /var/log/app.log whose level is ERROR, and write "
        "the counts keyed by hour as JSON into /out/errors.json.",
        "import json, collections\n"
        "def count_errors(log_path, out_path):\n"
        "    counts = collections.Counter()\n"
        "    for line in open(log_path):\n"
        "        stamp, level, _ = line.split(' ', 2)\n"
        "        if level == 'ERROR':\n"
        "            counts[stamp[:13]] += 1\n"
        "    json.dump(dict(counts), open(out_path, 'w'), sort_keys=True)\n",
    ),
    CorpusTask(
        "matrix-rotate",
        "Implement a function that rotates a square matrix by ninety degrees clockwise in place "
        "without allocating a second matrix.",
        "Write a function that rotates a square matrix ninety degrees clockwise, in place, and "
        "without allocating a second matrix.",
        "def rotate(grid):\n"
        "    n = len(grid)\n"
        "    for i in range(n):\n"
        "        for j in range(i + 1, n):\n"
        "            grid[i][j], grid[j][i] = grid[j][i], grid[i][j]\n"
        "    for row in grid:\n"
        "        row.reverse()\n",
    ),
    CorpusTask(
        "roman-numerals",
        "Convert every integer in input.txt to a Roman numeral and write one numeral per line "
        "to output.txt. Integers range from 1 to 3999.",
        "Every integer in input.txt is between 1 and 3999; write its Roman numeral to "
        "output.txt, one numeral per line.",
        "PAIRS = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'), (100, 'C'), (90, 'XC'),\n"
        "         (50, 'L'), (40, 'XL'), (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]\n"
        "def to_roman(number):\n"
        "    out = []\n"
        "    for value, letters in PAIRS:\n"
        "        while number >= value:\n"
        "            out.append(letters)\n"
        "            number -= value\n"
        "    return ''.join(out)\n",
    ),
    CorpusTask(
        "graph-shortest-path",
        "Given a weighted directed graph in edges.tsv, compute the shortest distance from node "
        "A to every other node with Dijkstra's algorithm and print unreachable nodes as inf.",
        "Use Dijkstra's algorithm on the weighted directed graph in edges.tsv to compute the "
        "shortest distance from node A to every other node; print inf for unreachable nodes.",
        "import heapq\n"
        "def dijkstra(edges, source):\n"
        "    dist = {source: 0}\n"
        "    heap = [(0, source)]\n"
        "    while heap:\n"
        "        d, node = heapq.heappop(heap)\n"
        "        if d > dist.get(node, float('inf')):\n"
        "            continue\n"
        "        for nxt, w in edges.get(node, []):\n"
        "            if d + w < dist.get(nxt, float('inf')):\n"
        "                dist[nxt] = d + w\n"
        "                heapq.heappush(heap, (d + w, nxt))\n"
        "    return dist\n",
    ),
    CorpusTask(
        "json-flatten",
        "Flatten the nested JSON object in config.json into dotted keys, so that a key b inside "
        "key a becomes a.b, and print the flattened object sorted by key.",
        "Print the object from config.json flattened into dotted keys and sorted by key; a key "
        "b nested inside key a becomes a.b.",
        "def flatten(obj, prefix=''):\n"
        "    flat = {}\n"
        "    for key, value in obj.items():\n"
        "        name = f'{prefix}.{key}' if prefix else key\n"
        "        if isinstance(value, dict):\n"
        "            flat.update(flatten(value, name))\n"
        "        else:\n"
        "            flat[name] = value\n"
        "    return flat\n",
    ),
    CorpusTask(
        "word-frequency",
        "Count word frequencies in book.txt, ignoring case and punctuation, and print the ten "
        "most common words with their counts, breaking ties alphabetically.",
        "Ignoring punctuation and case, count how often each word occurs in book.txt and print "
        "the ten most common words with their counts, ties broken alphabetically.",
        "import re, collections\n"
        "def top_words(text, k=10):\n"
        "    words = re.findall(r'[a-z]+', text.lower())\n"
        "    counts = collections.Counter(words)\n"
        "    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:k]\n",
    ),
    CorpusTask(
        "interval-merge",
        "Merge all overlapping intervals in intervals.json and write the merged list sorted by "
        "start. Intervals that only touch at an endpoint also merge.",
        "Write the intervals from intervals.json with all overlapping ones merged, sorted by "
        "start; two intervals that only touch at an endpoint merge as well.",
        "def merge(intervals):\n"
        "    merged = []\n"
        "    for start, end in sorted(intervals):\n"
        "        if merged and start <= merged[-1][1]:\n"
        "            merged[-1][1] = max(merged[-1][1], end)\n"
        "        else:\n"
        "            merged.append([start, end])\n"
        "    return merged\n",
    ),
    CorpusTask(
        "sqlite-top-customers",
        "Query the SQLite database shop.db and list the five customers with the highest total "
        "order value, printing name and total separated by a tab.",
        "List the five customers of the SQLite database shop.db whose orders have the highest "
        "total value; print the name and the total separated by a tab.",
        "import sqlite3\n"
        "def top_customers(db):\n"
        "    rows = sqlite3.connect(db).execute(\n"
        "        'SELECT c.name, SUM(o.amount) FROM customers c JOIN orders o '\n"
        "        'ON o.customer_id = c.id GROUP BY c.id ORDER BY 2 DESC LIMIT 5')\n"
        "    return ['%s\\t%s' % row for row in rows]\n",
    ),
    CorpusTask(
        "balanced-brackets",
        "Decide for each line of brackets.txt whether its round, square and curly brackets are "
        "balanced, and print yes or no per line.",
        "Print yes or no for every line of brackets.txt, depending on whether the round, square "
        "and curly brackets on it are balanced.",
        "PAIRS = {')': '(', ']': '[', '}': '{'}\n"
        "def balanced(text):\n"
        "    stack = []\n"
        "    for ch in text:\n"
        "        if ch in '([{':\n"
        "            stack.append(ch)\n"
        "        elif ch in PAIRS and (not stack or stack.pop() != PAIRS[ch]):\n"
        "            return False\n"
        "    return not stack\n",
    ),
    CorpusTask(
        "run-length-encoding",
        "Compress the string in plain.txt with run-length encoding, writing each run as the "
        "count followed by the character, and save the result to packed.txt.",
        "Save to packed.txt the run-length encoding of the string in plain.txt, where every "
        "run is written as its count followed by the character.",
        "import itertools\n"
        "def encode(text):\n"
        "    return ''.join(f'{len(list(g))}{ch}' for ch, g in itertools.groupby(text))\n",
    ),
    CorpusTask(
        "prime-sieve",
        "Print every prime number below the limit given on the command line using the sieve of "
        "Eratosthenes, one prime per line.",
        "Using the sieve of Eratosthenes, print one per line every prime below the limit that "
        "is passed on the command line.",
        "import sys\n"
        "def primes_below(limit):\n"
        "    sieve = bytearray([1]) * limit\n"
        "    sieve[:2] = b'\\x00\\x00'\n"
        "    for p in range(2, int(limit ** 0.5) + 1):\n"
        "        if sieve[p]:\n"
        "            sieve[p * p::p] = bytearray(len(sieve[p * p::p]))\n"
        "    return [i for i, is_prime in enumerate(sieve) if is_prime]\n",
    ),
]


def disguise(solution: str) -> str:
    """Rename every assigned or parameter identifier and add comments and blank lines."""
    found = re.findall(r"\b([a-z_][a-z0-9_]*)(?: = |, |\)| in )", solution)
    names = sorted(set(found) - set(dir(builtins)))
    renamed = solution
    for i, name in enumerate(names):
        renamed = re.sub(rf"\b{name}\b", f"renamed_{i}_{name[::-1]}", renamed)
    lines = renamed.splitlines()
    out = ["# resubmitted solution", ""]
    for line in lines:
        out.append(line + "  # step")
        if line.endswith(":"):
            continue
        out.append("")
    return "\n".join(out) + "\n"
