"""Security gate tests: the repo secret scanner must be clean AND must be
able to catch a planted leak (docs/09 stage 0: "secret scanner 可识别合成泄漏样例")."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNER = REPO_ROOT / "scripts" / "check-no-secrets.py"


def run_scanner(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 - scanner under test
        [sys.executable, str(SCANNER), "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_repository_secret_scan_is_clean() -> None:
    result = run_scanner(REPO_ROOT)
    assert result.returncode == 0, f"scanner findings:\n{result.stdout}\n{result.stderr}"


def test_scanner_detects_planted_real_secret(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "settings.py").write_text(
        'PROVIDER_API_KEY = "sk-abcdef0123456789abcdef0123456789"\n',
        encoding="utf-8",
    )
    result = run_scanner(tmp_path)
    assert result.returncode == 1
    assert "possible real credential assignment" in result.stdout


def test_scanner_flags_committed_env_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("PROVIDER_API_KEY=real-value-here-123456\n")
    result = run_scanner(tmp_path)
    assert result.returncode == 1
    assert "env file committed" in result.stdout


def test_scanner_allows_synthetic_values(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "settings.py").write_text(
        'DEMO_KEY = "MOCKKEY-DEMO-A-9f21"\n'
        'CANARY = "CANARY_NOT_REAL_ABC123"\n'
        'INTERNAL = "dev-internal-token-synthetic"\n',
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text("PP_SWITCH_MODE=local\nPP_SWITCH_WORKSPACE=demo\n")
    result = run_scanner(tmp_path)
    assert result.returncode == 0, result.stdout


def test_scanner_rejects_non_ppswitch_env_example(tmp_path: Path) -> None:
    (tmp_path / ".env.example").write_text("AWS_SECRET_ACCESS_KEY=abcdefghijklmnopqrst\n")
    result = run_scanner(tmp_path)
    assert result.returncode == 1
