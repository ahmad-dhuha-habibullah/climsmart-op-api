"""Artifact store. Local filesystem implementation now; the interface is what an S3-compatible store implements."""
from __future__ import annotations

import abc
import hashlib
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.util import new_id
from app.models import Artifact


class ArtifactStore(abc.ABC):
    backend: str

    @abc.abstractmethod
    def put_bytes(self, key: str, data: bytes) -> None: ...

    @abc.abstractmethod
    def put_file(self, key: str, src: Path) -> None: ...

    @abc.abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abc.abstractmethod
    def local_path(self, key: str) -> Path:
        """A readable local file for `key` (an S3 store would download to a cache dir)."""


class SupabaseArtifactStore(ArtifactStore):
    """Supabase Storage (private bucket) via its REST API with the service-role key; reads cached under /tmp."""

    backend = "supabase"

    def __init__(self, url: str, service_key: str, bucket: str, cache_dir: Path = Path("/tmp/climsmart-artifact-cache")):
        import httpx

        self.bucket, self.cache = bucket, Path(cache_dir)
        self._c = httpx.Client(base_url=url.rstrip("/") + "/storage/v1", timeout=60,
                               headers={"Authorization": f"Bearer {service_key}", "apikey": service_key})

    @staticmethod
    def _check(key: str) -> str:
        if key.startswith("/") or ".." in key.split("/"):
            raise ValueError("invalid artifact key")
        return key

    def put_bytes(self, key: str, data: bytes) -> None:
        r = self._c.post(f"/object/{self.bucket}/{self._check(key)}", content=data,
                         headers={"x-upsert": "true", "Content-Type": "application/octet-stream"})
        if r.status_code >= 300:
            raise RuntimeError(f"artifact upload failed: HTTP {r.status_code}")

    def put_file(self, key: str, src: Path) -> None:
        self.put_bytes(key, Path(src).read_bytes())

    def get_bytes(self, key: str) -> bytes:
        p = self.cache / self._check(key)
        if p.exists():
            return p.read_bytes()
        r = self._c.get(f"/object/authenticated/{self.bucket}/{key}")
        if r.status_code != 200:
            raise RuntimeError(f"artifact download failed: HTTP {r.status_code}")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(r.content)
        return r.content

    def local_path(self, key: str) -> Path:
        self.get_bytes(key)
        return self.cache / key


def make_store(settings) -> ArtifactStore:
    if settings.artifact_store == "supabase":
        if not (settings.supabase_url and settings.supabase_service_key):
            raise RuntimeError("ARTIFACT_STORE=supabase needs SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY")
        return SupabaseArtifactStore(settings.supabase_url, settings.supabase_service_key, settings.supabase_bucket)
    return LocalArtifactStore(settings.artifact_dir)


class LocalArtifactStore(ArtifactStore):
    backend = "local"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root.resolve() not in p.parents:
            raise ValueError("artifact key escapes the store root")
        return p

    def put_bytes(self, key: str, data: bytes) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def put_file(self, key: str, src: Path) -> None:
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, p)

    def get_bytes(self, key: str) -> bytes:
        return self._p(key).read_bytes()

    def local_path(self, key: str) -> Path:
        return self._p(key)


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def register_artifact(db: Session, store: ArtifactStore, *, kind: str, filename: str, media_type: str,
                      data: bytes | None = None, src: Path | None = None, plot_id: str | None = None,
                      job_id: str | None = None, provenance: dict | None = None) -> Artifact:
    """Store bytes (or a file) and record sha256, size, media type, plot context and provenance."""
    aid = new_id("art")
    key = f"{kind}/{aid}/{filename}"
    if data is not None:
        store.put_bytes(key, data)
        sha, size = hashlib.sha256(data).hexdigest(), len(data)
    elif src is not None:
        store.put_file(key, src)
        sha, size = _sha_file(src), Path(src).stat().st_size
    else:
        raise ValueError("data or src required")
    art = Artifact(id=aid, kind=kind, filename=filename, media_type=media_type, storage_backend=store.backend,
                   storage_key=key, sha256=sha, size_bytes=size, plot_id=plot_id, job_id=job_id,
                   provenance=provenance or {})
    db.add(art)
    db.flush()
    return art
