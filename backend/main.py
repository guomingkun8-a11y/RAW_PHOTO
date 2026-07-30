from __future__ import annotations

import sys
from pathlib import Path

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, access_log=False, log_level="info")
