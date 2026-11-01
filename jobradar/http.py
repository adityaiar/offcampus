from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})


def get(url: str, *, params=None, headers=None, timeout=20, retries=2, sleep=1.0):
    """GET with small retry/backoff. Returns Response or None (never raises for HTTP errors)."""
    for attempt in range(retries + 1):
        try:
            r = _session.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            return r
        except requests.RequestException as e:
            if attempt == retries:
                log.debug("GET failed %s: %s", url, e)
                return None
            time.sleep(sleep * (2 ** attempt))
    return None


def post_json(url: str, payload: dict, *, timeout=20, retries=2):
    for attempt in range(retries + 1):
        try:
            r = _session.post(url, json=payload, timeout=timeout,
                              headers={"Content-Type": "application/json", "Accept": "application/json"})
            if r.status_code == 429 or r.status_code >= 500:
                raise requests.HTTPError(f"HTTP {r.status_code}")
            return r
        except requests.RequestException as e:
            if attempt == retries:
                log.debug("POST failed %s: %s", url, e)
                return None
            time.sleep(1.0 * (2 ** attempt))
    return None
