# comment-budget

A [pre-commit](https://pre-commit.com) hook that ratchets a repo's comment-line count. The number may only go down.

It counts full-line comments in the tracked `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.yml`, `.yaml`, `.sh` and `.toml` files and compares the total to `.comment-budget`. It doesn't count these lines:

- docstrings
- comments after code on the same line
- shebangs and encoding lines
- lint and type-checker pragmas (`# noqa`, `// eslint-…`, `// @ts-…`, …)
- a license header at the top of a JS/TS file

## The budget file

`.comment-budget` holds one line:

| Content | Meaning |
| --- | --- |
| `412` | at most 412 comment lines |
| `3%` | comment lines at most 3% of code lines (non-blank lines that aren't counted comments) |
| `3% min=50` | 3%, but the first 50 comment lines are always allowed (helps small repos) |
| `3% max=6000` | 3%, and never more than 6000 comment lines |
| `3% min=50 max=6000` | both bounds |
| `off` | check disabled; it only prints the numbers |

`--write` lowers the percentage and `max=` to today's values and keeps `min=` as it is.

Ruff's `ERA` and `FIX` rules catch commented-out code and TODOs. This hook caps all the other comments.

## Use

```yaml
repos:
  - repo: https://github.com/FarisHijazi/comment-budget
    rev: v1.2.0
    hooks:
      - id: comment-budget
      - id: comment-budget-write # manual stage only
```

`pre-commit run comment-budget-write --hook-stage manual` lowers `.comment-budget` to the current count after you delete comments. Commit the file it writes.

Running the script directly:

```sh
./comment_budget.py --write               # create .comment-budget at today's count, then commit it
./comment_budget.py --list                # comment lines per file, highest first
./comment_budget.py --write               # lower the budget after deleting comments (keeps its units)
./comment_budget.py --write --percent     # switch to a percentage of code lines
./comment_budget.py --write --count --percent  # percentage plus max=
./comment_budget.py --write --force-raise # raise it, deliberately and loudly
```

To skip paths, list them in `.comment-budget-ignore` using gitignore-style globs (`vendor/`, `*.generated.ts`).

In CI, run it in your lint job with `pre-commit run --all-files`. You don't need a separate workflow. The script has no dependencies beyond `python3`.

## Test

```sh
uvx --with pytest pytest -q tests
```
