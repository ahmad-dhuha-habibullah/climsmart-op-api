from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()
