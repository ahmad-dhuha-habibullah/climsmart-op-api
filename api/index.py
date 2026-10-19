"""Vercel serverless entrypoint: exposes the FastAPI app as `app`.

Proxy trust: on Vercel the function is reachable only through Vercel's edge, which sets
X-Forwarded-For to the real client IP (it overwrites any client-supplied value). The direct peer
seen by the function is an internal, unpredictable address, so we trust any peer here; otherwise
all visitors would share one rate-limit bucket. Override with the CLIMSMART_TRUSTED_PROXIES env var.
Caveat: cache and rate limiter are per serverless instance (in memory), so limits are approximate.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if not os.environ.get("CLIMSMART_TRUSTED_PROXIES"):  # unset OR empty -> Vercel default (trust the edge)
    os.environ["CLIMSMART_TRUSTED_PROXIES"] = "0.0.0.0/0,::/0"

from app.main import create_combined_app  # noqa: E402

app = create_combined_app()
