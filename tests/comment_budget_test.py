import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "comment_budget.py"


def run(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, SCRIPT, *args], cwd=root, capture_output=True, text=True)


def repo(tmp_path: Path, files: dict[str, str]) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return tmp_path


def budgets(root: Path) -> str:
    return (root / ".budgets.toml").read_text()


def test_counts_only_full_line_comments(tmp_path):
    root = repo(tmp_path, {
        "a.py": '#!/usr/bin/env python3\n"""doc"""\n# one\nx = 1  # trailing\nimport os  # noqa\n# noqa: E501\n',
        "b.ts": "// Copyright acme\n\nconst a = 1;\n// two\n/* three\n   four */\n// eslint-disable-next-line\n",
        "c.sh": "#!/bin/sh\n# five\necho hi\n",
        ".comment-budget": "5\n",
    })
    out = run(root).stdout
    assert "5 comment lines" in out and "budget 5" in out


def test_markdown_comments_skip_fences_and_pragmas(tmp_path):
    md = (
        "# Title\n<!-- one -->\ntext\n<!--\ntwo\nthree -->\n"
        "```html\n<!-- in a fence, not counted -->\n```\n"
        "<!-- markdownlint-disable MD013 -->\n"
    )
    root = repo(tmp_path, {"a.md": md})
    out = run(root, "--comment-budget", "**/*.md=4").stdout
    assert "4 comment lines" in out


def test_legacy_file_ignores_markdown(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n", "a.md": "<!-- x -->\n", ".comment-budget": "1\n"})
    assert run(root).returncode == 0


def test_missing_budget_fails(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n"})
    assert run(root).returncode == 1


def test_write_creates_config(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n# d\n"})
    assert run(root, "--write").returncode == 0
    assert '"**" = "2"' in budgets(root)
    assert run(root).returncode == 0


def test_ratchet_legacy_file(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n# d\n", ".comment-budget": "2\n"})
    assert run(root).returncode == 0
    (root / "a.py").write_text("# c\n# d\n# e\n")
    assert run(root).returncode == 1
    assert run(root, "--write").returncode == 1
    assert run(root, "--write", "--force-raise").returncode == 0
    (root / "a.py").write_text("# c\n")
    assert run(root, "--write").returncode == 0
    assert (root / ".comment-budget").read_text() == "1\n"


def test_ignore_file(tmp_path):
    root = repo(tmp_path, {
        "a.py": "# c\n", "vendor/b.py": "# x\n# y\n",
        ".comment-budget-ignore": "vendor/\n", ".comment-budget": "1\n",
    })
    assert run(root).returncode == 0


def test_percent_ratchets_and_keeps_units(tmp_path):
    root = repo(tmp_path, {
        "a.py": "# c\nx = 1\ny = 2\nz = 3\n",
        ".budgets.toml": '[comment_budget]\n"**/*.py" = "34%"\n',
    })
    assert run(root, "--write").returncode == 0
    assert '"**/*.py" = "33.34%"' in budgets(root)
    (root / "a.py").write_text("# c\n# d\nx = 1\ny = 2\nz = 3\nw = 4\n")
    assert run(root).returncode == 1
    (root / "a.py").write_text("# c\nx = 1\ny = 2\nz = 3\nw = 4\nv = 5\n")
    assert run(root, "--write").returncode == 0
    assert '"**/*.py" = "20%"' in budgets(root)


def test_exact_percent_boundary(tmp_path):
    code = "".join(f"x{i} = {i}\n" for i in range(100))
    root = repo(tmp_path, {"a.py": "# c\n" * 29 + code, ".comment-budget": "29%\n"})
    assert run(root).returncode == 0


def test_max_hit_first(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n# d\n# e\n" + "x = 1\n" * 100, ".comment-budget": "25% max=2\n"})
    assert run(root).returncode == 1


def test_min_allows_before_percent_and_survives_write(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n# d\n# e\nx = 1\n", ".comment-budget": "3% min=3\n"})
    assert run(root).returncode == 0
    (root / "a.py").write_text("# c\n# d\n# e\n# f\n" + "x = 1\n" * 200)
    assert run(root, "--write").returncode == 0
    assert (root / ".comment-budget").read_text() == "2% min=3\n"


def test_off_entry(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n" * 50, ".comment-budget": "off\n"})
    assert run(root).returncode == 0
    assert run(root, "--write").returncode == 0
    assert (root / ".comment-budget").read_text() == "off\n"


def test_bad_value(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n", ".comment-budget": "min=5\n"})
    assert "bad budget" in run(root).stderr


def test_last_glob_wins_and_line_budget_sums(tmp_path):
    root = repo(tmp_path, {
        "README.md": "a\nb\nc\n",
        "docs/x.md": "a\nb\n",
        "docs/devlog/old.md": "a\n" * 500,
        "src/a.py": "x = 1\n" * 10,
        ".budgets.toml": (
            "# docs stay small\n[line_budget]\n"
            '"**/*.md" = "5"        # all docs\n'
            '"docs/devlog/**" = "off"\n'
        ),
    })
    assert run(root).returncode == 0
    (root / "docs/x.md").write_text("a\nb\nc\n")
    assert run(root).returncode == 1
    (root / "docs/x.md").write_text("a\n")
    assert run(root, "--write").returncode == 0
    text = budgets(root)
    assert '"**/*.md" = "4"        # all docs' in text and text.startswith("# docs stay small")
    assert '"docs/devlog/**" = "off"' in text


def test_line_budget_percent_of_repo_code(tmp_path):
    root = repo(tmp_path, {"a.md": "a\n" * 30, "a.py": "x = 1\n" * 100})
    assert run(root, "--line-budget", "**/*.md=30%").returncode == 0
    assert run(root, "--line-budget", "**/*.md=29%").returncode == 1


def test_flags_replace_config_and_cannot_write(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n# d\n", ".comment-budget": "1\n"})
    assert run(root).returncode == 1
    assert run(root, "--comment-budget", "**/*.py=2").returncode == 0
    assert run(root, "--comment-budget", "**/*.py=2", "--write").returncode != 0


def test_brace_and_star_globs(tmp_path):
    root = repo(tmp_path, {"a.ts": "// c\n", "b.js": "// d\n", "c.py": "# e\n"})
    out = run(root, "--comment-budget", "**/*.{ts,js}=2", "--comment-budget", "*.py=1").stdout
    assert "'**/*.{ts,js}': 2 comment lines" in out and "'*.py': 1 comment lines" in out


def test_config_file_itself_is_not_counted(tmp_path):
    root = repo(tmp_path, {
        "a.py": "# c\n",
        ".budgets.toml": '# header comment\n# another\n[comment_budget]\n"**" = "1"\n',
    })
    assert run(root).returncode == 0


def comments(root: Path, glob: str) -> str:
    return run(root, "--comment-budget", f"{glob}=0").stdout


def test_hcl_comments_skip_strings_heredocs_and_pragmas(tmp_path):
    tf = (
        "# one\n"
        "// two\n"
        "/* three\n"
        "   four */\n"
        'resource "x" "y" {\n'
        '  name = "# not a comment // nor this /* nor this"\n'
        '  esc  = "a \\" <<NOPE"\n'
        "  body = <<-EOT\n"
        "    # inside a heredoc\n"
        "    /* still inside\n"
        "    EOT\n"
        "  tag = 1 # trailing\n"
        "  x = 2 /* opens a block\n"
        "  five */\n"
        "}\n"
        "# tflint-ignore: terraform_unused_declarations\n"
        "#checkov:skip=CKV_X_1\n"
    )
    root = repo(tmp_path, {"main.tf": tf, "a.tfvars": "# six\nx = 1\n", "t.tftest.hcl": "// seven\nrun \"a\" {}\n"})
    out = comments(root, "**/*.{tf,tfvars,hcl}")
    assert "7 comment lines" in out and "of 14 code lines" in out


def test_hcl_heredoc_ends_only_at_its_marker(tmp_path):
    tf = 'a = <<EOT\n# in\nEOTX\n# still in\nEOT\n# out\n'
    root = repo(tmp_path, {"main.tf": tf})
    assert "1 comment lines" in comments(root, "**/*.tf")


def test_jinja_comments_and_inner_language(tmp_path):
    root = repo(tmp_path, {
        "run.sh.j2": "#!/bin/sh\n{# one #}\n# two\necho {{ x }}  # trailing\n{#\n  three\n  four\n#}\n",
        "first.sh.j2": "{# one #}\n#!/bin/bash\n# two\necho hi\n",
        "app.yml.j2": "# one\nkey: {{ v }} {# trailing #}\n{% if x %}\n# two\n{% endif %}\n",
        "x.conf.j2": "# one\nlisten 80;\n",
        "page.md.j2": "<!-- one -->\n# Title, not a comment\n",
    })
    for glob, n, code in [
        ("run.sh.j2", 6, 2), ("first.sh.j2", 2, 2), ("app.yml.j2", 2, 3), ("x.conf.j2", 1, 1), ("page.md.j2", 1, 1),
    ]:
        assert f"{n} comment lines ({0 if not code else n * 100 / code:.2f}% of {code} code lines)" in comments(root, glob), glob


def test_new_languages_ratchet(tmp_path):
    root = repo(tmp_path, {
        "main.tf": "# c\nx = 1\n",
        "a.sh.j2": "{# c #}\necho\n",
        ".budgets.toml": '[comment_budget]\n"**/*.tf" = "100%"\n"**/*.j2" = "100%"\n',
    })
    assert run(root, "--write").returncode == 0
    assert '"**/*.tf" = "100%"' in budgets(root) and '"**/*.j2" = "100%"' in budgets(root)
    (root / "main.tf").write_text("# c\n# d\nx = 1\n")
    assert run(root).returncode == 1
