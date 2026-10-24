"""Apply a seed file now (the server also applies ``data/seed/*.json`` on start-up).

    python scripts/load_seed.py seed/pathmind-seed.example.json
    python scripts/load_seed.py data/seed/team.json --force   # re-apply an unchanged file

See ``pathmind/services/seed.py`` for the format. Existing data is never
overwritten; generated passwords go to ``data/seed/seed-accounts.txt``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("file")
    parser.add_argument("--force", action="store_true", help="apply even if this exact file was applied")
    args = parser.parse_args()

    from pathmind.database.connection import init_database
    from pathmind.services import seed

    init_database()
    report = seed.apply_file(Path(args.file), force=args.force)
    if report is None:
        print("already applied (use --force to apply again)")
        return 0
    print(json.dumps({k: v for k, v in report.items() if v}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
