from __future__ import annotations

from fastapi import Query, Request

from app.core.errors import ApiError
from app.services.context import Context


def ctx(request: Request) -> Context:
    c = request.app.state.ctx
    if c is None:
        raise ApiError(503, "storage_not_configured", "v2 needs DATABASE_URL (and durable artifact storage) on this "
                                                      "deployment; the v1 API is unaffected",
                       {"init_error": getattr(request.app.state, "init_error", None)})
    return c


class Page:
    def __init__(self, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
        self.limit, self.offset = limit, offset


def paged(items, total, page: Page) -> dict:
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}
