# Percent budgets with absolute bounds (2026-11-02)

- `.comment-budget` accepts `N` (= `max=N`), `P%`, `P% min=N`, `P% max=N`, `P% min=N max=M`, or `off`.
- Allowed lines = clamp(P% x code, min, max): at or below `min=` always passes, above `max=` always fails.
  This replaced a short-lived `lower`/`higher` mode keyword: the two bounds express both and read plainly.
- Code lines = non-blank lines in counted files minus counted comment lines (docstrings count as code).
- Percent stored in hundredths, compared with integer math; `--write` rounds up to 0.01% so today's tree passes.
- `--write` keeps the file's units and `min=`; `--count` / `--percent` (or both) choose what to lower.
- Raising = "the current tree exceeds the old budget", which also covers switching units.
