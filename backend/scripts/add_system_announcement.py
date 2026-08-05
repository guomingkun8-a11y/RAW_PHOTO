from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.system_announcement_service import ANNOUNCEMENT_TYPES, system_announcement_service


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a system announcement shown in the notification bell.")
    parser.add_argument("--title", required=True, help="Announcement title.")
    parser.add_argument("--content", required=True, help="Announcement content.")
    parser.add_argument("--type", default="info", choices=sorted(ANNOUNCEMENT_TYPES), help="Announcement style.")
    parser.add_argument("--disabled", action="store_true", help="Create but do not show it to users.")
    parser.add_argument("--created-by", default="local-admin", help="Operator/user id recorded on the announcement.")
    args = parser.parse_args()

    item = system_announcement_service.create_announcement(
        title=args.title,
        content=args.content,
        announcement_type=args.type,
        enabled=not args.disabled,
        created_by=args.created_by,
    )
    print(f"created announcement #{item['id']}: {item['title']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
