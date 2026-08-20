from pathlib import Path


def test_flyhermes_quality_gate_uses_persistent_clafact_python() -> None:
    project_root = Path(__file__).resolve().parents[2]
    script = (project_root / "tools" / "run_flyhermes_quality_gate.sh").read_text(encoding="utf-8")

    assert "/opt/data/clafact_state/venvs/clafact-auto/bin/python" in script
    assert "/opt/data/.hermes/.env" in script
    assert "/opt/hermes/.venv" not in script
    assert "--env-file" in script
