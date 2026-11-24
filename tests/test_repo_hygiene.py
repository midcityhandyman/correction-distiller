"""Checks on the repository itself."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "build", "dist", ".deps"}
TEXT_SUFFIXES = {".py", ".md", ".toml", ".yml", ".yaml", ".txt", ".jsonl", ".json", ".example", ".gitignore"}


def _text_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or SKIP_DIRS & set(path.relative_to(ROOT).parts):
            continue
        if any(part.endswith(".egg-info") for part in path.parts):
            continue
        if path.suffix in TEXT_SUFFIXES or path.name in (".gitignore", ".env.example"):
            yield path


def test_no_long_dashes_anywhere():
    banned = (chr(0x2014), chr(0x2013))
    offenders = [str(p.relative_to(ROOT)) for p in _text_files()
                 if any(ch in p.read_text(encoding="utf-8") for ch in banned)]
    assert offenders == []


def test_required_files_exist():
    for name in ("README.md", ".gitignore", ".env.example", ".github/workflows/ci.yml",
                 "demo/run_demo.py", "demo/output.txt", "pyproject.toml"):
        assert (ROOT / name).is_file(), name
    assert (ROOT / "LICENSE").is_file()


def test_env_example_holds_no_real_key():
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if line.startswith("CD_LLM_API_KEY"):
            assert line.strip() == "CD_LLM_API_KEY="
