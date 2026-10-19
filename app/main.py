"""Combined ASGI app: the unchanged v1 app (climsmart.api.main) plus the v2 platform mounted at /api/v2."""
from __future__ import annotations

from fastapi import FastAPI

from app.api.v2 import create_v2_app
from climsmart.api.main import create_app


def create_combined_app(provider=None, settings=None, v2_ctx=None) -> FastAPI:
    app = create_app(provider=provider, settings=settings)
    v2 = create_v2_app(ctx=v2_ctx)
    # the v1 access-log middleware identifies clients via request.app.state; share what it needs with the sub-app
    v2.state.settings = app.state.settings
    v2.state.trusted_networks = app.state.trusted_networks
    app.mount("/api/v2", v2)
    return app


def get_app() -> FastAPI:  # uvicorn app.main:get_app --factory
    import logging

    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return create_combined_app()
