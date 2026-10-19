from __future__ import annotations

from dataclasses import dataclass

from app.core.config import V2Settings
from app.core.db import Database
from app.storage import ArtifactStore


@dataclass
class Context:
    settings: V2Settings
    db: Database
    store: ArtifactStore
