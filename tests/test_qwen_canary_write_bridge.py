import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("qwen_canary_write", ROOT / "templates" / "qwen_canary_write_bridge_server.py")
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


def repo(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    workdir = root / "pilot"
    (workdir / "tests").mkdir(parents=True)
    (workdir / ".git").mkdir()
    (workdir / "app.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (workdir / "tests" / "test_app.py").write_text("def test_add():\n    pass\n", encoding="utf-8")
    (workdir / "README.md").write_text("# Pilot\n", encoding="utf-8")
    monkeypatch.setattr(bridge, "ALLOWED_ROOT", root.resolve())
    return workdir


def test_read_and_exact_replace_only_allowed_files(tmp_path, monkeypatch):
    workdir = repo(tmp_path, monkeypatch)
    assert "def add" in bridge.read_file(str(workdir), "app.py")["content"]
    result = bridge.replace_exact(str(workdir), "app.py", "return a + b", "return a+b")
    assert result["replaced"] is True
    assert "return a+b" in (workdir / "app.py").read_text()


def test_read_and_exact_replace_allow_documentation_in_s04_scope(tmp_path, monkeypatch):
    workdir = repo(tmp_path, monkeypatch)
    assert bridge.read_file(str(workdir), "README.md")["content"] == "# Pilot\n"
    result = bridge.replace_exact(str(workdir), "README.md", "# Pilot", "# System One pilot")
    assert result["replaced"] is True
    assert (workdir / "README.md").read_text() == "# System One pilot\n"


@pytest.mark.parametrize("path", ["docs/README.md", ".git/config", "../app.py"])
def test_rejects_paths_outside_fixed_allowlist(tmp_path, monkeypatch, path):
    workdir = repo(tmp_path, monkeypatch)
    with pytest.raises(bridge.CanaryWriteError):
        bridge.read_file(str(workdir), path)


def test_replace_requires_one_exact_match(tmp_path, monkeypatch):
    workdir = repo(tmp_path, monkeypatch)
    with pytest.raises(bridge.CanaryWriteError):
        bridge.replace_exact(str(workdir), "app.py", "missing", "x")
