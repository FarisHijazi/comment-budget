# v2.0.0: per-glob comment and line budgets (2026-11-02)

- `.budgets.toml` with `[comment_budget]` and `[line_budget]` tables keyed by glob; same value grammar as v1.2.0.
- Last matching glob owns a file (gitignore/CODEOWNERS style), so a later `off` carves files out.
- `line_budget` % is of the repo's non-Markdown code lines (a docs-to-code ratio); `comment_budget` % is of the matched files' code lines.
- Markdown: `<!-- -->` lines count as comments, except inside code fences and lint/format pragmas.
- `--comment-budget` / `--line-budget GLOB=VALUE` flags replace the file; `--write` refuses on flags.
- `--write` rewrites values in place (keeps TOML comments/order); with no config it creates `.budgets.toml`.
- Legacy `.comment-budget` = `comment_budget "**/*.{py,ts,tsx,js,jsx,yml,yaml,sh,toml}"`, identical to v1 (verified same verdict as v1.2.0 on 5 real repos).
- Removed `--count` / `--percent`: edit the value's units and run `--write` instead.
- `tomllib` is imported lazily: python3 < 3.11 still runs flags and legacy files.
- Why not an existing tool: a survey found no linter rule, counter or ratchet tool with glob-summed budgets, percentages and a stored downward ratchet that runs offline.
