#!/usr/bin/env python3
"""Secret scanner for the pp switch repository (CI security gate).

Checks:
1. no secret-bearing files are committed (.env, *.key, *.pem, ...);
2. no real-looking credential assignments appear in tracked text files;
3. .env.example only contains non-secret PP_SWITCH_* configuration.

Known synthetic values (MOCKKEY-*, dev-token-*, CANARY_*, the dev internal
token, <REDACTED...> markers) are allow-listed: they exist precisely so
tests can prove that real secrets never take their place
(docs/11-TEST-PLAN.md section 3).

Usage: python scripts/check-no-secrets.py [--root PATH] [--verbose]
Exit code 0 = clean, 1 = findings, 2 = usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".ruff_cache", "dist", "build", "data", "htmlcov",
    ".idea", ".vscode", "coverage", "site-packages",
}

SECRET_FILE_PATTERNS = [
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
]
SECRET_FILE_SUFFIXES = (".key", ".pem", ".p12", ".pfx", ".keystore")
CREDENTIALS_NAME = re.compile(r"credentials.*\.json$", re.IGNORECASE)
SYNTHETIC_CREDENTIALS = "synthetic-credentials"

# value shapes that look like real credentials (assignments in code/config).
# The keyword may be a suffix of a longer name (PROVIDER_API_KEY, ...).
SECRET_ASSIGNMENT = re.compile(
    r"""(?ix)
    (api[_-]?key | secret | password | passwd | token | private[_-]?key |
      access[_-]?token | client[_-]?secret | session[_-]?id)
    \s*[:=]\s*["']([A-Za-z0-9+/_=-]{20,})["']
    """
)

ALLOWED_VALUE_PREFIXES = (
    "MOCKKEY-",
    "MOCK-",
    "dev-token-",
    "ci-internal-token-",
    "ci-ticket-key-",
    "dev-internal-token-synthetic",
    "CANARY_",
    "<REDACTED",
    "secretprovider://",
    "mock://",
)

# files where an assignment-looking line is documentation or test material
ALLOWED_PATH_PARTS = (
    "tests",
    "docs",
    "scripts",
    ".env.example",
    "example",
    "fixtures",
)


def is_secret_file(path: Path) -> str | None:
    name = path.name
    if name in SECRET_FILE_PATTERNS:
        return "env file committed"
    if name.endswith(SECRET_FILE_SUFFIXES):
        return f"secret-bearing file ({name})"
    if CREDENTIALS_NAME.search(name) and SYNTHETIC_CREDENTIALS not in name:
        return "credentials json committed"
    return None


def scan_text_file(path: Path, verbose: bool) -> list[str]:
    findings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:  # pragma: no cover - unreadable file
        return [f"{path}: unreadable ({exc})"]

    normalized = str(path).replace("\\", "/")
    in_allowed_location = any(part in normalized for part in ALLOWED_PATH_PARTS)

    for match in SECRET_ASSIGNMENT.finditer(text):
        value = match.group(2)
        if value.startswith(ALLOWED_VALUE_PREFIXES):
            continue
        if in_allowed_location:
            continue
        line_no = text.count("\n", 0, match.start()) + 1
        findings.append(
            f"{path}:{line_no}: possible real credential assignment "
            f"({match.group(1).lower()}=...)"
        )
    if verbose and not findings:
        print(f"  ok {path}")
    return findings


def check_env_example(root: Path) -> list[str]:
    env_example = root / ".env.example"
    if not env_example.exists():
        print("note: .env.example not present (fine for scans of sub-trees)")
        return []
    findings = []
    for line in env_example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if not key.startswith("PP_SWITCH_"):
            findings.append(f".env.example: non-PP_SWITCH key {key!r}")
        value = line.split("=", 1)[1] if "=" in line else ""
        if len(value) > 64:
            findings.append(f".env.example: suspicious long value for {key}")
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2

    findings: list[str] = []
    scanned = 0
    for path in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_dir():
            continue
        reason = is_secret_file(path)
        if reason:
            findings.append(f"{path}: {reason}")
            continue
        if path.suffix.lower() in (".py", ".ts", ".tsx", ".js", ".json", ".yaml",
                                    ".yml", ".toml", ".md", ".ini", ".cfg", ".sh",
                                    ".sql", ".txt", ".mako", ".example"):
            scanned += 1
            findings.extend(scan_text_file(path, args.verbose))

    findings.extend(check_env_example(root))

    print(f"scanned {scanned} text files under {root}")
    if findings:
        print(f"\nSECRET SCAN FAILED ({len(findings)} finding(s)):")
        for finding in findings:
            print(f"  {finding}")
        return 1
    print("secret scan clean: no committed credentials detected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
