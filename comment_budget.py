#!/usr/bin/env python3
"""Ratchet the number of comment lines in a repo: it may only go down.

Counts full-line comments in tracked source files and compares the total to the
integer in `.comment-budget`. Paths matching `.comment-budget-ignore`
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


def count_repo(root: Path) -> dict[str, int]:
    patterns = read_ignore(root)
    exts = PY_EXT | JS_EXT | HASH_EXT
    counts: dict[str, int] = {}
    for path in tracked_files(root):
        if Path(path).suffix.lower() not in exts or is_ignored(path, patterns):
            continue
        f = root / path
        if not f.is_file():
            continue
        n = count_file(path, f.read_text(encoding="utf-8", errors="replace"))
        if n:
            counts[path] = n
    return counts


def ranked(counts: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def read_budget(root: Path) -> int | None:
    f = root / BUDGET_FILE
    if not f.is_file():
        return None
    try:
        return int(f.read_text().strip())
    except ValueError:
        sys.exit(f"comment-budget: {BUDGET_FILE} must hold a single integer, got {f.read_text()!r}")


def write_budget(root: Path, budget: int | None, total: int, force_raise: bool) -> int:
    if budget is not None and total > budget:
        if not force_raise:
            print(
                f"comment-budget: refusing to RAISE the budget {budget} -> {total}. "
                "The budget only goes down; delete comments instead, "
                "or pass --force-raise if this is deliberate."
            )
            return 1
        print("!" * 72)
        print(f"!!! comment-budget: RAISING the budget {budget} -> {total} (+{total - budget}) !!!")
        print("!!! This loosens the ratchet for everyone; say why in the commit message.  !!!")
        print("!" * 72)
    (root / BUDGET_FILE).write_text(f"{total}\n")
    before = "unset" if budget is None else budget
    print(f"comment-budget: wrote {BUDGET_FILE} = {total} (was {before}). Commit it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="print per-file comment-line counts")
    ap.add_argument("--write", action="store_true", help=f"lower {BUDGET_FILE} to the current count")
    ap.add_argument("--force-raise", action="store_true", help="with --write, allow raising the budget")
    ap.add_argument("--root", type=Path, default=Path("."), help="repo root (default: cwd)")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    counts = count_repo(root)
    total = sum(counts.values())
    budget = read_budget(root)

    if args.list:
        for path, n in ranked(counts):
            print(f"{n:6d}  {path}")
        print(f"{total:6d}  TOTAL (budget {'unset' if budget is None else budget})")
        return 0
    if args.write:
        return write_budget(root, budget, total, args.force_raise)
    if budget is None:
        print(f"comment-budget: no {BUDGET_FILE} file. Run this script with --write to set it to {total}.")
        return 1
    if total > budget:
        print(f"comment-budget: FAILED - {total} comment lines, budget is {budget} (+{total - budget}).")
        print("Remove comments that restate the code. Top files by comment lines:")
        for path, n in ranked(counts)[:TOP_N]:
            print(f"{n:6d}  {path}")
        return 1
    print(f"comment-budget: {total} comment lines, budget {budget}.")
    if total < budget:
        print(f"Under budget by {budget - total}: run with --write to lock in the lower number.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
