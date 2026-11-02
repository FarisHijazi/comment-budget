#!/usr/bin/env python3
"""Ratchet comment lines and total lines per glob: budgets may only go down.

Budgets live in `.budgets.toml` (or are passed as flags), one table per metric, keyed by glob:

    [comment_budget]                # full-line comments; <!-- --> in Markdown
    "**/*.py" = "2% max=32"        # % of the matched files' code lines
    [line_budget]                   # every line, summed over the matched files
    "**/*.md" = "30%"              # % of the repo's (non-Markdown) code lines
    "docs/devlog/**" = "off"

A value is N, P%, 'P% min=N max=M' (min always passes, max always fails, % decides
between) or off. Each file belongs to the LAST glob that matches it, so a later `off`
carves files out of an earlier entry. A legacy `.comment-budget` file (one value)
still works and covers the original source files. `.comment-budget-ignore`
(gitignore-style) drops paths from every metric. Docstrings, trailing comments,
shebangs, encoding lines, lint pragmas and JS/TS license headers never count.

    comment_budget.py                           check
    comment_budget.py --list                    every budget with its files
    comment_budget.py --write                   lower budgets to today's values
    comment_budget.py --write --force-raise     raise them (loudly)
    comment_budget.py --comment-budget '**/*.py=2%' --line-budget '**/*.md=24000'
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

CONFIG_FILE = ".budgets.toml"
BUDGET_FILE = ".comment-budget"
IGNORE_FILE = ".comment-budget-ignore"
TOP_N = 10

PY_EXT = {".py"}
JS_EXT = {".ts", ".tsx", ".js", ".jsx"}
HASH_EXT = {".yml", ".yaml", ".sh", ".toml"}
MD_EXT = {".md"}

PY_PRAGMA = re.compile(r"#+\s*(noqa\b|type:\s*ignore|pragma:|ruff:|pylint:|fmt:|isort:|mypy:|pyright:)")
PY_ENCODING = re.compile(r"^[ \t\f]*#.*?coding[:=]")
JS_DIRECTIVE = re.compile(r"^(//+|/\*+)\s*(eslint|prettier-|@ts-|ts-|<reference\b)")
MD_PRAGMA = re.compile(r"<!--\s*(markdownlint|prettier-ignore|toc\b|/?toc\b|vale\b|cspell)", re.IGNORECASE)
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


def count_markdown(text: str) -> int:
    """Lines that start inside or at an HTML comment, outside fenced code blocks."""
    count, fence, in_comment = 0, None, False
    for line in text.splitlines():
        s = line.strip()
        if not in_comment and re.match(r"^(```|~~~)", s):
            fence = None if fence and s.startswith(fence) else (fence or s[:3])
            continue
        if fence:
            continue
        starts = in_comment or s.startswith("<!--")
        if starts and not (not in_comment and MD_PRAGMA.match(s)):
            count += 1
        if starts or "<!--" in s:
            in_comment = s.rfind("<!--") > s.rfind("-->") if "<!--" in s else in_comment and "-->" not in s
    return count


def count_file(path: str, text: str) -> int:
    ext = Path(path).suffix.lower()
    if ext in PY_EXT:
        return count_python(text)
    if ext in JS_EXT:
        return count_js(text)
    if ext in HASH_EXT:
        return count_hash(text)
    if ext in MD_EXT:
        return count_markdown(text)
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


class Budget:
    """Allowed amount = clamp(percent of base, floor, cap); `off` disables the entry.

    `cap` (max=N, or a bare N) always fails above it; `floor` (min=N) always passes at or
    below it; between the two the percentage (kept in hundredths) decides.
    """

    def __init__(self, cap: int | None = None, hundredths: int | None = None, floor: int | None = None, off: bool = False):
        self.cap, self.hundredths, self.floor, self.off = cap, hundredths, floor, off

    @classmethod
    def parse(cls, raw: str) -> Budget:
        tokens = raw.split()
        if tokens == [OFF]:
            return cls(off=True)
        fields: dict[str, str] = {}
        for tok in tokens:
            key = "pct" if tok.endswith("%") else tok.split("=", 1)[0] if "=" in tok else "max"
            if key not in ("pct", "min", "max") or key in fields:
                raise ValueError(raw)
            fields[key] = tok.rstrip("%").split("=", 1)[-1]
        if not fields or ("min" in fields and "pct" not in fields):
            raise ValueError(raw)
        return cls(
            int(fields["max"]) if "max" in fields else None,
            round(float(fields["pct"]) * 100) if "pct" in fields else None,
            int(fields["min"]) if "min" in fields else None,
        )

    def __str__(self) -> str:
        if self.off:
            return OFF
        if self.hundredths is None and self.floor is None:
            return str(self.cap)
        parts = [] if self.hundredths is None else [f"{self.hundredths / 100:g}%"]
        parts += [] if self.floor is None else [f"min={self.floor}"]
        parts += [] if self.cap is None else [f"max={self.cap}"]
        return " ".join(parts)

    def lowered(self, amount: int, base: int, cap: bool | None = None, percent: bool | None = None) -> Budget:
        if self.off:
            return self
        cap = self.cap is not None if cap is None else cap
        percent = self.hundredths is not None if percent is None else percent
        hundredths = -(-amount * 10000 // base) if base else 0
        return Budget(amount if cap else None, hundredths if percent else None, self.floor)

    def exceeded(self, amount: int, base: int) -> bool:
        if self.off:
            return False
        if self.cap is not None and amount > self.cap:
            return True
        if self.hundredths is None or (self.floor is not None and amount <= self.floor):
            return False
        return amount * 10000 > self.hundredths * base

    def under(self, amount: int, base: int) -> bool:
        if self.off:
            return False
        now = self.lowered(amount, base, True, True)
        return (self.cap is not None and amount < self.cap) or (
            self.hundredths is not None and now.hundredths < self.hundredths
        )


OFF = "off"
METRICS = ("comment_budget", "line_budget")
LEGACY_GLOB = "**/*.{py,ts,tsx,js,jsx,yml,yaml,sh,toml}"
ENTRY = re.compile(r'^(\s*"((?:[^"\\]|\\.)*)"\s*=\s*")([^"]*)(".*)$')


def glob_regex(pattern: str) -> re.Pattern[str]:
    """Full-path glob: `**/` any directories, `*` within one segment, `?`, `{a,b}`."""
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out, i = out + "(?:.*/)?", i + 3
        elif pattern.startswith("**", i):
            out, i = out + ".*", i + 2
        elif pattern[i] == "*":
            out, i = out + "[^/]*", i + 1
        elif pattern[i] == "?":
            out, i = out + "[^/]", i + 1
        elif pattern[i] == "{" and "}" in pattern[i:]:
            j = pattern.index("}", i)
            out, i = out + "(?:" + "|".join(re.escape(p) for p in pattern[i + 1 : j].split(",")) + ")", j + 1
        else:
            out, i = out + re.escape(pattern[i]), i + 1
    return re.compile(out + r"\Z")


def assign(paths: list[str], globs: list[str]) -> dict[str, list[str]]:
    """Each path belongs to the LAST glob that matches it (like .gitignore / CODEOWNERS)."""
    compiled = [(g, glob_regex(g)) for g in globs]
    owned: dict[str, list[str]] = {g: [] for g in globs}
    for path in paths:
        owner = next((g for g, rx in reversed(compiled) if rx.match(path)), None)
        if owner is not None:
            owned[owner].append(path)
    return owned


def parse_flag(raw: str) -> tuple[str, Budget]:
    glob, sep, value = raw.partition("=")
    if not sep or not glob:
        sys.exit(f"comment-budget: expected GLOB=VALUE, got {raw!r}")
    return glob, parse_value(value, raw)


def parse_value(value: str, where: str) -> Budget:
    try:
        return Budget.parse(value)
    except ValueError:
        sys.exit(
            f"comment-budget: bad budget {value!r} in {where}: use N, P%, 'P% min=N max=M' or {OFF}"
        )


def load_config(root: Path, args: argparse.Namespace) -> tuple[dict[str, dict[str, Budget]], Path | None]:
    """Budgets per metric per glob, and the file they came from (None = CLI flags)."""
    if args.comment_budget or args.line_budget:
        return {
            "comment_budget": dict(parse_flag(r) for r in args.comment_budget),
            "line_budget": dict(parse_flag(r) for r in args.line_budget),
        }, None
    toml_file = root / CONFIG_FILE
    if toml_file.is_file():
        try:
            import tomllib
        except ModuleNotFoundError:
            sys.exit(f"comment-budget: {CONFIG_FILE} needs python3 >= 3.11 (tomllib); use flags or {BUDGET_FILE}.")
        data = tomllib.loads(toml_file.read_text())
        unknown = set(data) - set(METRICS)
        if unknown:
            sys.exit(f"comment-budget: unknown table(s) {sorted(unknown)} in {CONFIG_FILE}; use {METRICS}")
        return {m: {g: parse_value(str(v), f"{CONFIG_FILE} [{m}] {g!r}") for g, v in data.get(m, {}).items()} for m in METRICS}, toml_file
    legacy = root / BUDGET_FILE
    if legacy.is_file():
        raw = legacy.read_text().strip()
        return {"comment_budget": {LEGACY_GLOB: parse_value(raw, BUDGET_FILE)}, "line_budget": {}}, legacy
    return {"comment_budget": {}, "line_budget": {}}, None


def measure(root: Path) -> dict[str, tuple[int, int, int]]:
    """path -> (comment lines, code lines, total lines) for every tracked, non-ignored file."""
    patterns = read_ignore(root)
    out: dict[str, tuple[int, int, int]] = {}
    for path in tracked_files(root):
        f = root / path
        if is_ignored(path, patterns) or not f.is_file():
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        n = count_file(path, text)
        out[path] = (n, code_lines(text, n) if counted(path) else 0, len(text.splitlines()))
    return out


def counted(path: str) -> bool:
    return Path(path).suffix.lower() in PY_EXT | JS_EXT | HASH_EXT | MD_EXT


def evaluate(config: dict[str, dict[str, Budget]], stats: dict[str, tuple[int, int, int]]):
    """Yield (metric, glob, budget, amount, base, files) for every entry."""
    repo_code = sum(c for p, (_, c, _) in stats.items() if Path(p).suffix.lower() not in MD_EXT)
    for metric in METRICS:
        entries = config[metric]
        paths = [p for p in stats if metric == "line_budget" or counted(p)]
        for glob, files in assign(paths, list(entries)).items():
            if metric == "comment_budget":
                amount = sum(stats[p][0] for p in files)
                base = sum(stats[p][1] for p in files)
            else:
                amount = sum(stats[p][2] for p in files)
                base = repo_code
            yield metric, glob, entries[glob], amount, base, files


def rewrite_toml(text: str, new: dict[tuple[str, str], Budget]) -> str:
    """Replace only the values of known entries, keeping order, comments and layout."""
    table, out = None, []
    for line in text.splitlines(keepends=True):
        header = re.match(r"^\s*\[([^\]]+)\]", line)
        if header:
            table = header.group(1).strip()
        m = ENTRY.match(line.rstrip("\n"))
        if m and (table, m.group(2)) in new:
            line = f"{m.group(1)}{new[(table, m.group(2))]}{m.group(4)}" + ("\n" if line.endswith("\n") else "")
        out.append(line)
    return "".join(out)


def write(source: Path, results: list, force_raise: bool) -> int:
    raising = [r for r in results if r[2].exceeded(r[3], r[4])]
    if raising and not force_raise:
        for metric, glob, budget, amount, base, _ in raising:
            print(f"comment-budget: refusing to RAISE {metric} {glob!r}: {budget} -> {budget.lowered(amount, base)}.")
        print("The budget only goes down; delete lines instead, or pass --force-raise if this is deliberate.")
        return 1
    if raising:
        print("!" * 72)
        print("!!! comment-budget: RAISING budgets; say why in the commit message. !!!")
        print("!" * 72)
    new = {(m, g): b.lowered(a, base) for m, g, b, a, base, _ in results}
    if source.name == BUDGET_FILE:
        source.write_text(f"{new[('comment_budget', LEGACY_GLOB)]}\n")
    else:
        source.write_text(rewrite_toml(source.read_text(), new))
    for (m, g), b in new.items():
        print(f"comment-budget: {m} {g!r} = {b}")
    print(f"comment-budget: wrote {source.name}. Commit it.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--comment-budget", action="append", default=[], metavar="GLOB=VALUE",
                    help="comment-line budget for a glob (repeatable; replaces the config file)")
    ap.add_argument("--line-budget", action="append", default=[], metavar="GLOB=VALUE",
                    help="total-line budget for a glob (repeatable; replaces the config file)")
    ap.add_argument("--list", action="store_true", help="print every budget with its top files")
    ap.add_argument("--write", action="store_true", help="lower every budget in the config file to today's value")
    ap.add_argument("--force-raise", action="store_true", help="with --write, allow raising a budget")
    ap.add_argument("--root", type=Path, default=Path("."), help="repo root (default: cwd)")
    args = ap.parse_args(argv)

    root = args.root.resolve()
    config, source = load_config(root, args)
    if args.write and source is None and not (args.comment_budget or args.line_budget):
        (root / CONFIG_FILE).write_text('[comment_budget]\n"**" = "0"\n')
        config, source = load_config(root, args)
        args.force_raise = True
    if not any(config.values()):
        print(f"comment-budget: no budgets. Add {CONFIG_FILE} (or {BUDGET_FILE}, or --comment-budget/--line-budget flags).")
        return 1
    stats = measure(root)
    results = list(evaluate(config, stats))

    if args.write:
        if source is None:
            sys.exit("comment-budget: --write needs a config file; budgets given as flags are edited by hand.")
        return write(source, results, args.force_raise)

    failed = False
    for metric, glob, budget, amount, base, files in results:
        unit = "comment lines" if metric == "comment_budget" else "lines"
        ratio = f"{(amount * 100 / base if base else 0):.2f}% of {base} code lines"
        if budget.off:
            print(f"  off   {metric} {glob!r}: {amount} {unit} ({ratio})")
            continue
        over = budget.exceeded(amount, base)
        failed |= over
        print(f"  {'FAIL' if over else 'ok':4}  {metric} {glob!r}: {amount} {unit} ({ratio}), budget {budget}")
        if over or args.list:
            col = 0 if metric == "comment_budget" else 2
            top = sorted(((stats[p][col], p) for p in files if stats[p][col]), reverse=True)
            for n, p in top[: None if args.list else TOP_N]:
                print(f"        {n:6d}  {p}")
        elif budget.under(amount, base) and source is not None:
            print("        under budget: run with --write to lock in the lower number")
    if failed:
        print("comment-budget: FAILED. Delete lines that restate the code or the docs.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
