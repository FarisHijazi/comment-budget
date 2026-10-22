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


def test_counts_only_full_line_comments(tmp_path):
    root = repo(tmp_path, {
        "a.py": '#!/usr/bin/env python3\n"""doc"""\n# one\nx = 1  # trailing\nimport os  # noqa\n# noqa: E501\n',
        "b.ts": "// Copyright acme\n\nconst a = 1;\n// two\n/* three\n   four */\n// eslint-disable-next-line\n",
        "c.sh": "#!/bin/sh\n# five\necho hi\n",
    })
    out = run(root, "--list").stdout
    assert "5  TOTAL" in out


def test_missing_budget_fails(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n"})
    assert run(root).returncode == 1


def test_ratchet(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n# d\n"})
    assert run(root, "--write").returncode == 0
    assert (root / ".comment-budget").read_text() == "2\n"
    assert run(root).returncode == 0

    (root / "a.py").write_text("# c\n# d\n# e\n")
    assert run(root).returncode == 1
    assert run(root, "--write").returncode == 1
    assert run(root, "--write", "--force-raise").returncode == 0

    (root / "a.py").write_text("# c\n")
    assert run(root, "--write").returncode == 0
    assert (root / ".comment-budget").read_text() == "1\n"


def test_ignore_file(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n", "vendor/b.py": "# x\n# y\n", ".comment-budget-ignore": "vendor/\n"})
    assert "1  TOTAL" in run(root, "--list").stdout
