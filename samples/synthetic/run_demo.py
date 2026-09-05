"""One-command demo: generate synthetic documents, ingest them, review the round (mock LLM).

    python samples/synthetic/run_demo.py

Uses exactly the CLI a user would type. NDT_HCX_MOCK=1 makes every LLM stage return
tests/fixtures/hcx_mock.json, so it runs offline and deterministically.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SYN = ROOT / "samples" / "synthetic"
PY = sys.executable


def run(*args: str) -> str:
    env = {**os.environ, "NDT_HCX_MOCK": "1", "PYTHONUTF8": "1"}
    print("$", " ".join(args))
    r = subprocess.run([PY, "-m", "app.main", *args], cwd=ROOT, env=env, capture_output=True, text=True)
    out = (r.stdout + r.stderr)
    tail = "\n".join(l for l in out.splitlines() if not l.startswith("INFO ndt:"))[-2500:]
    print(tail)
    if r.returncode != 0:
        raise SystemExit(f"command failed: {' '.join(args)}")
    return out


def main() -> None:
    subprocess.run([PY, str(SYN / "make_synthetic.py")], check=True)
    run("ingest-drawings", str(SYN / "drawings"), "--as-of", "2026-09-01")
    run("ingest-standards", str(SYN / "scwep"), "--type", "scwep")
    run("review", "--billing", str(SYN / "billing" / "round1_CP-P1.xlsx"),
        "--reports", str(SYN / "reports" / "round1_reports.pdf"),
        "--round", "1", "--date", "2026-09-03", "--discipline", "CP-P1")


if __name__ == "__main__":
    main()
