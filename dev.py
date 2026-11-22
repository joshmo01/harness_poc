"""Cross-platform task runner (use this on Windows, where `make` isn't available).

    python dev.py setup   # install Python + web dependencies
    python dev.py run     # stubs :8101, gateway :8100, BFF :8000
    python dev.py web     # React app on :5173 (second terminal)
    python dev.py test    # governance tests
    python dev.py data    # regenerate synthetic data
    python dev.py pin     # re-pin approved tool definitions
    python dev.py e2e     # browser walkthrough (needs run + web, and playwright)
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
PY = sys.executable
NPM = shutil.which("npm") or shutil.which("npm.cmd") or "npm"


def sh(cmd, cwd=ROOT):
    print("+", " ".join(map(str, cmd)))
    subprocess.run(cmd, cwd=cwd, check=True)


TASKS = {
    "setup": lambda: (sh([PY, "-m", "pip", "install", "-r", "requirements.txt"]), sh([NPM, "ci"], cwd=WEB)),
    "run": lambda: sh([PY, "-m", "poc.run_all"]),
    "web": lambda: sh([NPM, "run", "dev"], cwd=WEB),
    "test": lambda: sh([PY, "-m", "pytest", "-q", "tests/test_gateway.py"]),
    "data": lambda: sh([PY, "-m", "poc.data.generate"]),
    "pin": lambda: sh([PY, "-m", "poc.gateway.registry", "--pin"]),
    "e2e": lambda: sh([PY, "tests/e2e/demo_walkthrough.py"]),
}

if __name__ == "__main__":
    task = sys.argv[1] if len(sys.argv) > 1 else ""
    if task not in TASKS:
        print(__doc__)
        sys.exit(1)
    try:
        TASKS[task]()
    except KeyboardInterrupt:
        pass
