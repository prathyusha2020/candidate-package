#!/usr/bin/env python3
"""One command that runs every check. Needs no API key (everything replays from cache/).

    python run_all_tests.py
"""
import filecmp
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NO_KEY = {**os.environ, "OPENAI_API_KEY": ""}          # empty on purpose: prove nothing needs the key
results = []


def step(name, cmd, must_contain=None):
    print(f"\n=== {name} ===\n$ {' '.join(str(c) for c in cmd)}")
    p = subprocess.run([str(c) for c in cmd], cwd=ROOT, env=NO_KEY, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = p.stdout + p.stderr
    print(out.strip()[-1500:])
    ok = p.returncode == 0 and (must_contain is None or must_contain in out)
    results.append((name, ok))
    return ok


def main() -> int:
    py = sys.executable
    with tempfile.TemporaryDirectory() as d:
        replay = Path(d) / "parsed.json"
        step("1. Unit tests (no model)", [py, "-m", "unittest", "discover", "tests"], "OK")
        step("2. Replay the dev inbox with no key: 0 model calls", [py, "-m", "rfq", "parse", "--emails", "data/dev/emails", "--out", replay], "model calls this run: 0")
        same = replay.exists() and filecmp.cmp(replay, ROOT / "parsed.json", shallow=False)
        print("replayed output is identical to the committed parsed.json:", same)
        results.append(("3. Replay equals committed parsed.json", same))
        step("4. Output format (their checker)", [py, "data/check_format.py", replay, "--emails", "data/dev/emails"], "0 problem(s)")
        step("5. Score against the dev answer key", [py, "evaluate.py", replay, "-v"], "silent losses      0")
        step("6. 24 hand-written edge-case emails", [py, "tests/run_scenarios.py"], "24/24 scenarios as expected")
    print("\n=== SUMMARY ===")
    for name, ok in results:
        print(("PASS  " if ok else "FAIL  ") + name)
    return 0 if all(ok for _, ok in results) else 1


if __name__ == "__main__":
    sys.exit(main())
