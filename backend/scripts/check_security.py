from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


BACKEND_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.security_config import find_embedded_secret_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Report secrets embedded in JSON configuration files.")
    parser.add_argument("--config", type=Path, default=ROOT_DIR / "config.json")
    parser.add_argument("--strict", action="store_true", help="Exit with status 1 when findings exist.")
    args = parser.parse_args()

    data = json.loads(args.config.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("configuration root must be a JSON object")

    findings = find_embedded_secret_paths(data)
    if not findings:
        print("security configuration check passed: no embedded secrets found")
        return

    print(f"security configuration check found {len(findings)} embedded secret source(s):")
    for path in findings:
        print(f"- {path}")
    print("move these values to environment variables and rotate the provider credentials")
    if args.strict:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
