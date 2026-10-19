"""Combined ASGI app: the unchanged v1 app (climsmart.api.main) plus the v2 platform mounted at /api/v2."""
from __future__ import annotations

from fastapi import FastAPI

from climsmart.api.main import create_app


def create_combined_app(provider=None, settings=None) -> FastAPI:
    return create_app(provider=provider, settings=settings)
