# comment-budget

A [pre-commit](https://pre-commit.com) hook that ratchets two numbers per glob: **comment lines** and **total lines**. A budget may only go down.

## Budgets

Put them in `.budgets.toml` at the repo root, one table per metric, keyed by glob:

```toml
[comment_budget]            # full-line comments
"**" = "2% max=32"          # % of the matched files' code lines

[line_budget]               # every line, summed over the matched files
"**/*.md" = "50%"           # docs: % of the repo's (non-Markdown) code lines
"docs/devlog/**" = "off"    # devlogs exempt
"CLAUDE.md" = "300"         # a glob can be a single file
```

**Each file belongs to the last glob that matches it**, like `.gitignore` or CODEOWNERS. That's how `off` carves files out of an earlier entry.

A value is one of:

| Value | Meaning |
| --- | --- |
| `412` | at most 412 lines |
| `3%` | at most 3% of the base (code lines; see the table comments above) |
| `3% min=50` | 3%, but the first 50 lines always pass (helps small repos) |
| `3% max=6000` | 3%, and never more than 6000 |
| `3% min=50 max=6000` | both bounds |
| `off` | not checked; the numbers are still printed |

Globs match the full repo-relative path: `**/` is any number of directories, `*` stays within one directory, `?` is one character, and `{a,b}` is either.

### What counts as a comment

Full-line comments in `.py`, `.ts`, `.tsx`, `.js`, `.jsx`, `.yml`, `.yaml`, `.sh`, `.toml`, and `<!-- -->` lines in `.md`. These don't count:

- docstrings
- comments after code on the same line
- shebangs and encoding lines
- lint and type-checker pragmas (`# noqa`, `// eslint-…`, `// @ts-…`, `<!-- markdownlint-… -->`, `<!-- prettier-ignore -->`, …)
- a license header at the top of a JS/TS file
- HTML comments inside fenced code blocks in Markdown

Code lines are non-blank lines that aren't counted comments. Markdown prose counts as code lines of the `.md` file for `comment_budget`. It never counts toward the repo's code lines, which are the base of a `line_budget` percentage.

### Flags instead of a file

`--comment-budget GLOB=VALUE` and `--line-budget GLOB=VALUE` can be repeated, and replace the config file when given. They suit a hook's `args:`. `--write` can't edit flags, so you lower those by hand.

### Legacy `.comment-budget`

A one-value `.comment-budget` file still works. It means `comment_budget "**/*.{py,ts,tsx,js,jsx,yml,yaml,sh,toml}"`, exactly what v1 counted, without Markdown. `.comment-budget-ignore` (gitignore-style) drops paths from every metric.

Ruff's `ERA` and `FIX` rules catch commented-out code and TODOs. This hook caps everything else.

## Use

```yaml
repos:
  - repo: https://github.com/FarisHijazi/comment-budget
    rev: v2.0.0
    hooks:
      - id: comment-budget
      - id: comment-budget-write # manual stage only
      # or, without a config file:
      # - id: comment-budget
      #   args: [--comment-budget, "**=2%", --line-budget, "**/*.md=50%"]
```

`pre-commit run comment-budget-write --hook-stage manual` lowers every budget in the config file to today's value. It keeps each value's units and `min=`, and leaves the file's comments and layout untouched. Commit what it writes.

```sh
./comment_budget.py                          # check
./comment_budget.py --list                   # every budget with its files, largest first
./comment_budget.py --write                  # lower budgets (creates .budgets.toml if there is none)
./comment_budget.py --write --force-raise    # raise them, deliberately and loudly
```

To change units (for example from a count to a percentage), edit the value and run `--write`.

In CI, run it in your lint job with `pre-commit run --all-files`. You don't need a separate workflow. It needs only `python3` 3.11 or newer, for `tomllib`.

## Test

```sh
uvx --with pytest pytest -q tests
```
