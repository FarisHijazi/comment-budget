# v2.1.0: OpenTofu/Terraform and Jinja comments (2026-11-27)

- `.tf`, `.tfvars`, `.hcl`: full-line `#`, `//`, `/* */`. A line scanner tracks `"..."` strings (with `\` escapes) only to find heredoc openers (`<<EOT`, `<<-EOT`) and a `/*` left open after code; heredoc bodies never count.
- `.j2`: `{# #}` lines are counted, removed, and the rest is counted as the inner extension's language (`#` if unknown), so a shebang after a leading `{# #}` is still line 1 and excluded.
- tflint/checkov/tfsec/trivy pragmas don't count; `.terraform.lock.hcl` headers do (exclude with `off` or `.comment-budget-ignore`).
- Repos with a `"**"` comment budget now also count these files; the legacy `.comment-budget` glob is unchanged.
- No library: pygments would add the repo's first dependency to a `language: script` hook.
