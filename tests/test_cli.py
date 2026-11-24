import json
import subprocess
import sys
from pathlib import Path

import pytest

from correction_distiller.cli import run
from correction_distiller.config import Settings
from correction_distiller.rulepack import load_rule_pack

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(tmp_path, raw, clock):
    """Run CLI commands against a throwaway database; returns (exit code, output lines)."""
    data = tmp_path / "corrections.jsonl"
    data.write_text("\n".join(json.dumps(r) for r in raw), encoding="utf-8")
    settings = Settings(db_path=str(tmp_path / "state" / "distiller.db"), owners=("owner",))

    def call(*argv):
        lines = []
        code = run(list(argv), settings=settings, out=lines.append, now=clock)
        return code, lines

    call.data = str(data)
    call.tmp = tmp_path
    return call


def _pending_ids(cli):
    _, lines = cli("queue")
    return [line.split()[1] for line in lines if line.startswith("[pending]")]


def test_full_loop_through_the_cli(cli):
    code, lines = cli("ingest", cli.data)
    assert code == 0 and lines[0] == "added 9, duplicates 0, refused 0"

    code, lines = cli("distill")
    assert code == 0 and "proposed 3" in lines[0]
    ids = _pending_ids(cli)
    assert len(ids) == 3

    code, lines = cli("show", ids[0])
    assert code == 0 and "evidence (3):" in "\n".join(lines)

    assert cli("approve", ids[0], "--by", "owner")[0] == 0
    assert cli("approve", ids[1], "--by", "owner", "--edit", "No customer texts after 8pm.")[0] == 0
    assert cli("reject", ids[2], "--by", "owner", "--reason", "tone")[0] == 0
    assert cli("queue")[1] == ["no pending proposals"]
    assert len(cli("queue", "--status", "all")[1]) == 3

    out = cli.tmp / "pack"
    code, lines = cli("export", "--out", str(out))
    assert code == 0 and lines[0].startswith("wrote 2 rule(s)")
    pack = load_rule_pack(out / "rulepack.json")
    assert [r["approved_by"] for r in pack["rules"]] == ["owner", "owner"]
    assert pack["rules"][1]["text"] == "No customer texts after 8pm."

    code, lines = cli("pack", str(out / "rulepack.json"), "--evidence")
    assert code == 0 and lines[0].startswith("Standing rules (2)")

    assert cli("retire", ids[0], "--by", "owner", "--reason", "policy changed")[0] == 0
    cli("export", "--out", str(out))
    assert load_rule_pack(out / "rulepack.json")["rule_count"] == 1

    code, lines = cli("log")
    actions = [line.split()[2] for line in lines]
    assert actions.count("propose") == 3 and "approve" in actions and "retire" in actions


def test_errors_exit_nonzero_with_a_message(cli):
    cli("ingest", cli.data)
    cli("distill")
    pid = _pending_ids(cli)[0]
    code, lines = cli("approve", pid, "--by", "intern-bot")
    assert code == 1 and "not a configured owner" in lines[0]
    code, lines = cli("approve", "p_missing", "--by", "owner")
    assert code == 1 and "no proposal" in lines[0]
    assert cli("show", "p_missing")[0] == 1
    code, lines = cli("ingest", str(cli.tmp / "missing.jsonl"))
    assert code == 1 and lines[0].startswith("error:")
    assert cli("pack", str(cli.tmp / "missing.json"))[0] == 1


def test_non_utf8_input_is_an_error_not_a_traceback(cli):
    path = cli.tmp / "utf16.jsonl"
    path.write_text('{"quote": "Never text a customer after 8pm."}\n', encoding="utf-16")
    code, lines = cli("ingest", str(path))
    assert code == 1 and lines == [lines[0]] and "not UTF-8" in lines[0]


def test_dropped_command_lists_nothing_by_default(cli):
    cli("ingest", cli.data)
    cli("distill")
    assert cli("dropped")[1] == ["nothing dropped"]


def test_module_entry_point_runs_offline(tmp_path):
    """`python -m correction_distiller` works as a subprocess with no keys set."""
    env = {"PYTHONPATH": str(ROOT / "src"), "SYSTEMROOT": __import__("os").environ.get("SYSTEMROOT", ""),
           "PATH": __import__("os").environ.get("PATH", "")}
    db = str(tmp_path / "d.db")
    base = [sys.executable, "-m", "correction_distiller", "--db", db]
    first = subprocess.run(base + ["ingest", str(ROOT / "demo" / "corrections_week1.jsonl")],
                           capture_output=True, text=True, env=env, cwd=tmp_path)
    assert first.returncode == 0, first.stderr
    assert "added 14, duplicates 0, refused 2" in first.stdout
    second = subprocess.run(base + ["distill"], capture_output=True, text=True, env=env, cwd=tmp_path)
    assert second.returncode == 0, second.stderr
    assert "proposed 5" in second.stdout


def test_demo_output_file_matches_a_fresh_run():
    """demo/output.txt is the real captured output, not a hand written sample."""
    result = subprocess.run([sys.executable, str(ROOT / "demo" / "run_demo.py")],
                            capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    saved = (ROOT / "demo" / "output.txt").read_text(encoding="utf-8")
    assert result.stdout.replace("\r\n", "\n") == saved.replace("\r\n", "\n")
