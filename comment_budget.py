#!/usr/bin/env python3
"""Ratchet the number of comment lines in a repo: it may only go down.

Counts full-line comments in tracked source files and compares the total to
`.comment-budget`, which holds a count (`412`), a percentage of code lines
(`4.5%`; code = non-blank lines that are not counted comments) with optional
absolute bounds (`4.5% min=50 max=6000`: up to 50 always pass, over 6000 always
fail, the percentage decides in between), or `off` to disable the check. Paths matching `.comment-budget-ignore`
(gitignore-style globs) are skipped. Docstrings and trailing comments after
code are never counted; neither are shebangs, encoding lines, lint/type-checker
pragmas, or a license header at the top of a JS/TS file.

    comment_budget.py               check: fail if count > budget
    comment_budget.py --list        per-file counts, highest first
    comment_budget.py --write       lower the budget to the current count
    comment_budget.py --write --force-raise   raise it (loudly)
"""

from __future__ import annotations

import argparse
import fnmatch
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

BUDGET_FILE = ".comment-budget"
IGNORE_FILE = ".comment-budget-ignore"
TOP_N = 10

PY_EXT = {".py"}
JS_EXT = {".ts", ".tsx", ".js", ".jsx"}
HASH_EXT = {".yml", ".yaml", ".sh", ".toml"}

PY_PRAGMA = re.compile(r"#+\s*(noqa\b|type:\s*ignore|pragma:|ruff:|pylint:|fmt:|isort:|mypy:|pyright:)")
PY_ENCODING = re.compile(r"^[ \t\f]*#.*?coding[:=]")
JS_DIRECTIVE = re.compile(r"^(//+|/\*+)\s*(eslint|prettier-|@ts-|ts-|<reference\b)")
LICENSE = re.compile(r"licen[cs]e|copyright|spdx-", re.IGNORECASE)


def count_python(text: str) -> int:
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, SyntaxError):
        return count_hash(text)
    count = 0
    for tok in tokens:
        if tok.type != tokenize.COMMENT or tok.line[: tok.start[1]].strip():
            continue
        row = tok.start[0]
        if row == 1 and tok.string.startswith("#!"):
            continue
        if row <= 2 and PY_ENCODING.match(tok.string):
            continue
        if PY_PRAGMA.match(tok.string):
            continue
        count += 1
    return count


def count_hash(text: str) -> int:
    lines = text.splitlines()
    return sum(
        1
        for i, line in enumerate(lines)
        if line.lstrip().startswith("#") and not (i == 0 and line.startswith("#!"))
    )


def js_comment_lines(text: str) -> list[tuple[int, str]]:
    """(line index, stripped text) of every full-line // or /* */ comment line."""
    out: list[tuple[int, str]] = []
    in_block = False
    block_is_directive = False
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        if in_block:
            if not block_is_directive:
                out.append((i, s))
            in_block = "*/" not in s
        elif s.startswith("//"):
            if not JS_DIRECTIVE.match(s):
                out.append((i, s))
        elif s.startswith("/*"):
            block_is_directive = bool(JS_DIRECTIVE.match(s))
            if not block_is_directive:
                out.append((i, s))
            in_block = "*/" not in s[2:]
    return out


def count_js(text: str) -> int:
    comments = js_comment_lines(text)
    comment_rows = {row for row, _ in comments}
    lines = text.splitlines()
    header: list[int] = []
    for i, line in enumerate(lines):
        if not line.strip() or (i == 0 and line.startswith("#!")):
            continue
        if header and i != header[-1] + 1:
            break
        if i in comment_rows:
            header.append(i)
        else:
            break
    if header and LICENSE.search("\n".join(lines[j] for j in header)):
        skip = set(header)
        comments = [c for c in comments if c[0] not in skip]
    return len(comments)


def count_file(path: str, text: str) -> int:
    ext = Path(path).suffix.lower()
    if ext in PY_EXT:
        return count_python(text)
    if ext in JS_EXT:
        return count_js(text)
    if ext in HASH_EXT:
        return count_hash(text)
    return 0


def read_ignore(root: Path) -> list[str]:
    f = root / IGNORE_FILE
    if not f.is_file():
        return []
    lines = (ln.strip() for ln in f.read_text().splitlines())
    return [ln for ln in lines if ln and not ln.startswith("#")]


def is_ignored(path: str, patterns: list[str]) -> bool:
    parts = path.split("/")
    for raw in patterns:
        dir_only = raw.endswith("/")
        pat = raw.rstrip("/")
        anchored = "/" in pat
        pat = pat.lstrip("/")
        last = len(parts) - 1 if dir_only else len(parts)
        if anchored:
            if any(fnmatch.fnmatchcase("/".join(parts[: i + 1]), pat) for i in range(last)):
                return True
        elif any(fnmatch.fnmatchcase(part, pat) for part in parts[:last]):
            return True
    return False


def tracked_files(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    return [p for p in out.split("\0") if p]


def code_lines(text: str, comments: int) -> int:
    return max(0, sum(1 for ln in text.splitlines() if ln.strip()) - comments)


def count_repo(root: Path) -> dict[str, tuple[int, int]]:
    patterns = read_ignore(root)
    exts = PY_EXT | JS_EXT | HASH_EXT
    counts: dict[str, tuple[int, int]] = {}
    for path in tracked_files(root):
        if Path(path).suffix.lower() not in exts or is_ignored(path, patterns):
            continue
        f = root / path
        if not f.is_file():
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        n = count_file(path, text)
        counts[path] = (n, code_lines(text, n))
    return counts


def ranked(counts: dict[str, tuple[int, int]]) -> list[tuple[str, int]]:
    return sorted(((p, n) for p, (n, _) in counts.items() if n), key=lambda kv: (-kv[1], kv[0]))


class Budget:
    """Allowed comment lines = clamp(percent of code lines, floor, cap).

    `cap` (max=N, or a bare N) always fails above it; `floor` (min=N) always passes at or
    below it; between the two the percentage (kept in hundredths) decides.
    """

    def __init__(self, cap: int | None = None, hundredths: int | None = None, floor: int | None = None):
        self.cap, self.hundredths, self.floor = cap, hundredths, floor

    def __str__(self) -> str:
        if self.hundredths is None and self.floor is None:
            return str(self.cap)
        parts = [] if self.hundredths is None else [f"{self.hundredths / 100:g}%"]
        parts += [] if self.floor is None else [f"min={self.floor}"]
        parts += [] if self.cap is None else [f"max={self.cap}"]
        return " ".join(parts)

    def lowered(self, comments: int, code: int, cap: bool, percent: bool) -> Budget:
        hundredths = -(-comments * 10000 // code) if code else 0
        return Budget(comments if cap else None, hundredths if percent else None, self.floor)

    def exceeded(self, comments: int, code: int) -> bool:
        if self.cap is not None and comments > self.cap:
            return True
        if self.hundredths is None or (self.floor is not None and comments <= self.floor):
            return False
        return comments * 10000 > self.hundredths * code

    def under(self, comments: int, code: int) -> bool:
        now = self.lowered(comments, code, True, True)
        return (self.cap is not None and comments < self.cap) or (
            self.hundredths is not None and now.hundredths < self.hundredths
        )


OFF = "off"


def read_budget(root: Path) -> Budget | str | None:
    f = root / BUDGET_FILE
    if not f.is_file():
        return None
    raw = f.read_text().split()
    if raw == [OFF]:
        return OFF
    fields: dict[str, str] = {}
    for tok in raw:
        key = "pct" if tok.endswith("%") else tok.split("=", 1)[0] if "=" in tok else "max"
        if key not in ("pct", "min", "max") or key in fields:
            fields = {}
            break
        fields[key] = tok.rstrip("%").split("=", 1)[-1]
    try:
        if not fields or ("min" in fields and "pct" not in fields):
            raise ValueError
        return Budget(
            int(fields["max"]) if "max" in fields else None,
            round(float(fields["pct"]) * 100) if "pct" in fields else None,
            int(fields["min"]) if "min" in fields else None,
        )
    except ValueError:
        sys.exit(
            f"comment-budget: {BUDGET_FILE} must hold a count (412), a percentage with optional "
            f"absolute bounds (4.5% min=50 max=6000) or {OFF}, got {raw!r}"
        )


def write_budget(root: Path, budget: Budget | None, new: Budget, raising: bool, force_raise: bool) -> int:
    if raising:
        if not force_raise:
            print(
                f"comment-budget: refusing to RAISE the budget {budget} -> {new}. "
                "The budget only goes down; delete comments instead, "
                "or pass --force-raise if this is deliberate."
            )
            return 1
        print("!" * 72)
        print(f"!!! comment-budget: RAISING the budget {budget} -> {new} !!!")
        print("!!! This loosens the ratchet for everyone; say why in the commit message.  !!!")
        print("!" * 72)
    (root / BUDGET_FILE).write_text(f"{new}\n")
    print(f"comment-budget: wrote {BUDGET_FILE} = {new} (was {budget or 'unset'}). Commit it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="print per-file comment-line counts")
    ap.add_argument("--write", action="store_true", help=f"lower {BUDGET_FILE} to the current value")
    ap.add_argument("--percent", action="store_true", help="with --write, set the percentage (keeps min=; with --count also max=)")
    ap.add_argument("--count", action="store_true", help="with --write, set the absolute max (with --percent: both)")
    ap.add_argument("--force-raise", action="store_true", help="with --write, allow raising the budget")
    ap.add_argument("--root", type=Path, default=Path("."), help="repo root (default: cwd)")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    counts = count_repo(root)
    comments = sum(n for n, _ in counts.values())
    code = sum(c for _, c in counts.values())
    ratio = f"{Budget().lowered(comments, code, False, True).hundredths / 100:g}%"
    budget = read_budget(root)

    if args.list:
        for path, n in ranked(counts):
            print(f"{n:6d}  {path}")
        print(f"{comments:6d}  TOTAL comment lines, {code} code lines ({ratio}) (budget {budget or 'unset'})")
        return 0
    if budget == OFF and not (args.write and (args.count or args.percent)):
        print(f"comment-budget: disabled ({BUDGET_FILE} says {OFF}). {comments} comment lines / {code} code lines ({ratio}).")
        return 0
    if args.write:
        known = isinstance(budget, Budget)
        if args.count or args.percent:
            cap, percent = args.count, args.percent
        elif known:
            cap, percent = budget.cap is not None, budget.hundredths is not None
        else:
            cap, percent = True, False
        new = (budget if known else Budget()).lowered(comments, code, cap, percent)
        raising = known and budget.exceeded(comments, code)
        return write_budget(root, budget, new, raising, args.force_raise)
    if budget is None:
        print(f"comment-budget: no {BUDGET_FILE} file. Run this script with --write (or --write --percent).")
        return 1
    summary = f"{comments} comment lines / {code} code lines ({ratio})"
    if budget.exceeded(comments, code):
        print(f"comment-budget: FAILED - {summary}, budget is {budget}.")
        print("Remove comments that restate the code. Top files by comment lines:")
        for path, n in ranked(counts)[:TOP_N]:
            print(f"{n:6d}  {path}")
        return 1
    print(f"comment-budget: {summary}, budget {budget}.")
    if budget.under(comments, code):
        print("Under budget: run with --write to lock in the lower number.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
