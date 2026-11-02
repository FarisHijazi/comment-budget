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


def test_percent_budget(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\nx = 1\ny = 2\nz = 3\n"})
    assert run(root, "--write", "--percent").returncode == 0
    assert (root / ".comment-budget").read_text() == "33.34%\n"
    assert run(root).returncode == 0

    (root / "a.py").write_text("# c\n# d\nx = 1\ny = 2\nz = 3\nw = 4\n")
    assert run(root).returncode == 1
    assert run(root, "--write").returncode == 1

    (root / "a.py").write_text("# c\nx = 1\ny = 2\nz = 3\nw = 4\nv = 5\n")
    assert run(root).returncode == 0
    assert run(root, "--write").returncode == 0
    assert (root / ".comment-budget").read_text() == "20%\n"


def test_switch_units(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\nx = 1\n", ".comment-budget": "1\n"})
    assert run(root, "--write", "--percent").returncode == 0
    assert (root / ".comment-budget").read_text() == "100%\n"
    assert run(root, "--write", "--count").returncode == 0
    assert (root / ".comment-budget").read_text() == "1\n"


def test_exact_percent_boundary(tmp_path):
    code = "".join(f"x{i} = {i}\n" for i in range(100))
    root = repo(tmp_path, {"a.py": "# c\n" * 29 + code, ".comment-budget": "29%\n"})
    assert run(root).returncode == 0


def test_percent_with_max_fails_whichever_first(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n# d\n" + "x = 1\n" * 8, ".comment-budget": "25% max=2\n"})
    assert run(root).returncode == 0

    (root / "a.py").write_text("# c\n# d\n" + "x = 1\n" * 6)
    assert run(root).returncode == 1, "percent hit first"

    (root / "a.py").write_text("# c\n# d\n# e\n" + "x = 1\n" * 100)
    assert run(root).returncode == 1, "max hit first"

    (root / "a.py").write_text("# c\n" + "x = 1\n" * 10)
    assert run(root, "--write").returncode == 0
    assert (root / ".comment-budget").read_text() == "10% max=1\n"


def test_min_allows_before_percent(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n# d\n# e\nx = 1\n", ".comment-budget": "3% min=3\n"})
    assert run(root).returncode == 0, "3 lines is within min= even at 300%"

    (root / "a.py").write_text("# c\n# d\n# e\n# f\nx = 1\n")
    assert run(root).returncode == 1, "above min=, the percentage applies"

    (root / "a.py").write_text("# c\n# d\n# e\n# f\n" + "x = 1\n" * 200)
    assert run(root).returncode == 0
    assert run(root, "--write").returncode == 0
    assert (root / ".comment-budget").read_text() == "2% min=3\n", "write keeps min="


def test_off_disables(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n" * 50, ".comment-budget": "off\n"})
    assert run(root).returncode == 0
    assert "disabled" in run(root).stdout
    assert run(root, "--write").returncode == 0
    assert (root / ".comment-budget").read_text() == "off\n"


def test_bad_budget_file(tmp_path):
    root = repo(tmp_path, {"a.py": "# c\n", ".comment-budget": "min=5\n"})
    assert "must hold" in run(root).stderr
