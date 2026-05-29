"""
Main entrypoint — run with:
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

import sys
import os

# Make sure all local packages are importable
sys.path.insert(0, os.path.dirname(__file__))

from api.server import app  # noqa: F401 — uvicorn targets this

if __name__ == "__main__":
    import uvicorn
    from config.settings import settings

    uvicorn.run(
        "main:app",
        host=settings.app_host,
        port=settings.app_port,
        reload=False,
        log_level="info",
    )
