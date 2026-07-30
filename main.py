import uvicorn

from backend.main import app

__all__ = ["app"]

if __name__ == "__main__":
    uvicorn.run(app, access_log=False, log_level="info")
