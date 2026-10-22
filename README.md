# comment-budget

A [pre-commit](https://pre-commit.com) hook that ratchets a repo's comment-line count. The number may only go down.

It counts full-line comments in the tracked `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.yml`, `.yaml`, `.sh` and `.toml` files and compares the total to the integer in `.comment-budget`. It doesn't count these lines:

- docstrings
- comments after code on the same line
- shebangs and encoding lines
- lint and type-checker pragmas (`# noqa`, `// eslint-…`, `// @ts-…`, …)
- a license header at the top of a JS/TS file

Ruff's `ERA` and `FIX` rules catch commented-out code and TODOs. This hook caps all the other comments.

## Use

```yaml
repos:
  - repo: https://github.com/FarisHijazi/comment-budget
    rev: v1.1.0
    hooks:
      - id: comment-budget
      - id: comment-budget-write # manual stage only
```

`pre-commit run comment-budget-write --hook-stage manual` lowers `.comment-budget` to the current count after you delete comments. Commit the file it writes.

Running the script directly:

```sh
./comment_budget.py --write               # create .comment-budget at today's count, then commit it
./comment_budget.py --list                # comment lines per file, highest first
./comment_budget.py --write               # lower the budget after deleting comments
./comment_budget.py --write --force-raise # raise it, deliberately and loudly
```

To skip paths, list them in `.comment-budget-ignore` using gitignore-style globs (`vendor/`, `*.generated.ts`).

In CI, run it in your lint job with `pre-commit run --all-files`. You don't need a separate workflow. The script has no dependencies beyond `python3`.

## Test

```sh
uvx --with pytest pytest -q tests
```
