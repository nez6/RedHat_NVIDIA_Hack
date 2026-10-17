"""Tiny stdlib HTTP helper with retries (no third-party deps so CI stays fast)."""
import json, time, urllib.parse, urllib.request

from .config import HTTP_TIMEOUT, USER_AGENT


def get(url, params=None, retries=3, backoff=1.5, as_json=True):
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
                body = r.read().decode("utf-8")
            return json.loads(body) if as_json else body
        except Exception as e:  # network, HTTP or JSON errors
            last = e
            time.sleep(backoff ** attempt)
    raise RuntimeError(f"GET failed after {retries} tries: {url[:120]} ({last})")
