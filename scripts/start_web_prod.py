#!/usr/bin/env python
"""Serve the PathMind web app from an optimized production build.

``next dev`` compiles every page on first visit, ships unminified React in
development mode and keeps HMR machinery running — that is what makes the
app feel sluggish (10+ s first loads, janky navigation). This runner builds
the frontend once into ``web/.next-pathmind`` and serves the standalone
production server instead.

The build is reused until a file under ``web/`` changes (same fingerprint the
``pathmind start`` launcher uses), so the first start after an update takes a
few minutes and later starts are instant. ``start.bat dev`` keeps the
development server for frontend work.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pathmind.runtime import launcher  # noqa: E402


def _truthy(value: str | None, default: bool) -> bool:
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def main() -> int:
    source = PROJECT_ROOT / "web"
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        print("Node.js/npm not found on PATH.", flush=True)
        return 1

    port = os.environ.get("FRONTEND_PORT") or "3782"
    api_base = os.environ.get("PATHMIND_API_BASE_URL") or "http://127.0.0.1:8001"
    auth_enabled = _truthy(os.environ.get("PATHMIND_AUTH_ENABLED"), True)

    started = time.monotonic()
    print("Checking the production build of the web app ...", flush=True)
    launcher._ensure_web_dependencies(source, npm)
    launcher._ensure_source_production_build(
        source, npm, api_base=api_base, auth_enabled=auth_enabled
    )
    print(f"Production build ready ({time.monotonic() - started:.0f}s).", flush=True)

    env = launcher._source_production_env(api_base=api_base, auth_enabled=auth_enabled)
    env["PORT"] = str(port)
    # Listen on the LAN too (phones / tablets), like `next dev` did.
    env["HOSTNAME"] = os.environ.get("FRONTEND_HOST") or "0.0.0.0"
    env["NODE_ENV"] = "production"
    env.setdefault("PATHMIND_API_BASE_URL", api_base)

    standalone = source / launcher.SOURCE_PRODUCTION_DIST_DIR / "standalone"
    print(f"PathMind web (production) on http://localhost:{port}", flush=True)
    return subprocess.call([node, "server.js"], cwd=standalone, env=env)


if __name__ == "__main__":
    sys.exit(main())
