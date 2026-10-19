"""GitHub Actions APSIM runner: dispatch `apsim-run.yml`; the workflow fetches the bundle and posts results back.

Auth between workflow and API: shared secret APSIM_CALLBACK_SECRET in header X-ClimSmart-Callback-Secret
(constant-time compared). Requires durable storage: the callback may hit a different serverless instance.
"""
from __future__ import annotations

import hmac
import io
import tarfile

import httpx

from app.core.errors import ApiError

SECRET_HEADER = "X-ClimSmart-Callback-Secret"
GITHUB_API = "https://api.github.com"


class DispatchFailed(Exception):
    pass


def check_ready(settings) -> None:
    if settings.ephemeral:
        raise ApiError(409, "persistent_storage_required",
                       "the GitHub Actions APSIM runner needs a persistent DATABASE_URL and artifact store; this "
                       "deployment uses ephemeral /tmp storage, so the workflow callback would lose the run")
    missing = [n for n, v in (("GITHUB_DISPATCH_TOKEN", settings.github_dispatch_token),
                              ("APSIM_CALLBACK_SECRET", settings.apsim_callback_secret),
                              ("PUBLIC_BASE_URL", settings.public_base_url)) if not v]
    if missing:
        raise ApiError(503, "actions_runner_not_configured",
                       "APSIM_RUNNER=actions but required settings are missing", {"missing": missing})


def run_url(settings, run_id: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/api/v2/apsim/runs/{run_id}"


def dispatch(settings, run_id: str, client: httpx.Client | None = None) -> str:
    url = f"{GITHUB_API}/repos/{settings.github_repo}/actions/workflows/{settings.github_workflow}/dispatches"
    body = {"ref": settings.github_ref, "inputs": {"run_id": run_id, "callback_url": run_url(settings, run_id)}}
    headers = {"Authorization": f"Bearer {settings.github_dispatch_token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    c = client or httpx.Client(timeout=20)
    try:
        resp = c.post(url, json=body, headers=headers)
    except httpx.HTTPError as e:
        raise DispatchFailed(f"GitHub API unreachable: {e.__class__.__name__}") from e
    finally:
        if client is None:
            c.close()
    if resp.status_code not in (200, 204):
        raise DispatchFailed(f"GitHub workflow dispatch failed: HTTP {resp.status_code} {resp.text[:200]}")
    return f"https://github.com/{settings.github_repo}/actions/workflows/{settings.github_workflow}"


def verify_secret(settings, presented: str | None) -> None:
    expected = settings.apsim_callback_secret
    if not expected or not presented or not hmac.compare_digest(presented.encode(), expected.encode()):
        raise ApiError(401, "invalid_callback_secret", "missing or invalid runner secret")


def bundle(apsimx: bytes, met: bytes, met_filename: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in (("simulation.apsimx", apsimx), (met_filename, met)):
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode = len(data), 0o644
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()
