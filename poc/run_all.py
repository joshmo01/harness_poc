"""Run stubs (8101), gateway (8100) and BFF (8000) in one process for local dev.

    python -m poc.run_all
"""
import threading
import time

import uvicorn

from poc.common.config import DB_PATH, REGISTRY_PATH

SERVICES = [("poc.stubs.servers:app", 8101), ("poc.gateway.app:app", 8100), ("poc.bff.app:app", 8000)]


def main() -> None:
    if not DB_PATH.exists():
        from poc.data.generate import build
        build()
    if not REGISTRY_PATH.exists():
        from poc.gateway.registry import pin
        pin()
    for target, port in SERVICES:
        cfg = uvicorn.Config(target, host="0.0.0.0", port=port, log_level="warning")
        threading.Thread(target=uvicorn.Server(cfg).run, daemon=True).start()
        print(f"  {target:<28} http://127.0.0.1:{port}")
    print("Running. Web app: cd web && npm run dev  ->  http://localhost:5173")
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
