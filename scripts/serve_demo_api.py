"""Serve the original API with explicit synthetic demo adapters, without a GPU."""

from __future__ import annotations

import os

import uvicorn

if __name__ == "__main__":
    os.environ["CAREFLOW_DEMO_MODE"] = "1"
    from careflow_api.main import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=8000, timeout_graceful_shutdown=3)
