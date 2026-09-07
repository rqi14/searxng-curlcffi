"""Anubis proof-of-work solver hooked into SearXNG's curl_cffi session.

Upstream SearXNG dropped httpx for curl_cffi on 2026-09-03 (`[mod] network:
migrate to curl_cffi`), so browser TLS/JA3 impersonation is now native and the
transport this file used to wrap no longer exists. What is still missing is the
proof-of-work gate: chrome fingerprinting gets past Startpage's bot wall but
lands on a 22 KB Anubis JS shell that no engine parser can read.

`install()` wraps `AsyncClient.request`, the one place every non-streaming
engine request goes through (searx/network/network.py `call_client`), so every
engine benefits and no parser has to learn what a challenge page looks like.
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from searx.network.anubis import extract_challenge, pass_challenge_url

logger = logging.getLogger("searx.network.anubis")

# Only sniff bodies that could plausibly be a challenge page. Anubis serves a
# small self-contained HTML shell; skipping everything else keeps the fast path
# (real search results, often hundreds of KB) free of extra decoding.
MAX_SNIFF_BYTES = 256 * 1024

# Carried across to the redeem hop so it looks like the same client. Anubis
# binds the challenge to the User-Agent (it echoes it in challenge.metadata).
_FORWARD = ("user-agent", "accept-language", "accept")

# SearXNG builds its session with discard_cookies=True, so the clearance cookie
# is never kept for us — capture and replay it by hand. Cached per host so a
# cleared site costs one PoW, not one per search.
_CLEARANCE: dict[str, str] = {}


def install(client_cls: type) -> None:
    """Wrap ``client_cls.request`` so Anubis walls are solved transparently."""
    original = client_cls.request
    if getattr(original, "_anubis", False):
        return

    async def request(self, method: str, url: str, **kwargs):
        # pylint: disable=too-many-return-statements
        host = urlsplit(str(url)).netloc
        cookie = _CLEARANCE.get(host)
        if cookie:
            kwargs = _with_cookie(kwargs, cookie)

        response = await original(self, method, url, **kwargs)

        # A streaming response has no body to sniff yet, and reading it here
        # would consume the very stream the caller is about to iterate.
        if kwargs.get("stream") or method.upper() not in ("GET", "POST"):
            return response
        blob = _challenge_of(response)
        if blob is None:
            return response
        redeem = pass_challenge_url(str(response.url), blob)
        if redeem is None:
            return response

        # The challenge page sets cookies that bind the solution to the issued
        # challenge (sp_pow, spchal-cookie-verification on Startpage). Redeeming
        # without them is rejected with HTTP 500. Don't follow the redirect back
        # to `redir` either — the clearance arrives as Set-Cookie on the 3xx.
        issued = _cookie_header(response)
        headers = {k: v for k, v in _headers_of(kwargs).items() if k.lower() in _FORWARD}
        if issued:
            headers["cookie"] = issued
        try:
            redeemed = await original(self, "GET", redeem, headers=headers, allow_redirects=False)
        except Exception as exc:  # pragma: no cover - network fault, serve the wall
            logger.warning("anubis: redeem failed (%s: %s), returning challenge page", type(exc).__name__, exc)
            return response

        cookie = "; ".join(c for c in (issued, _cookie_header(redeemed)) if c)
        logger.debug("anubis: redeem status=%s cookie=%s", redeemed.status_code, cookie[:120] or "(none)")
        if not cookie:
            return response

        _CLEARANCE[host] = cookie
        retried = await original(self, method, url, **_with_cookie(kwargs, cookie))
        if _challenge_of(retried) is not None:
            # Don't cache a clearance that demonstrably did not clear.
            _CLEARANCE.pop(host, None)
            logger.warning("anubis: still challenged after redeem for %s", host)
        else:
            logger.info("anubis: cleared %s", host)
        return retried

    request._anubis = True  # pylint: disable=protected-access
    client_cls.request = request


def _challenge_of(response) -> dict | None:
    """Parse the Anubis challenge out of a response, or None if it isn't one."""
    if "html" not in response.headers.get("content-type", ""):
        return None
    body = response.content
    if len(body) > MAX_SNIFF_BYTES:
        return None
    return extract_challenge(body.decode("utf-8", "replace"))


def _headers_of(kwargs: dict) -> dict[str, str]:
    return dict(kwargs.get("headers") or {})


def _cookie_header(response) -> str:
    """Fold a response's Set-Cookie headers into one Cookie value."""
    crumbs = [v.split(";", 1)[0].strip() for v in response.headers.get_list("set-cookie")]
    return "; ".join(c for c in crumbs if "=" in c)


def _with_cookie(kwargs: dict, cookie: str) -> dict:
    """Copy kwargs with the clearance cookie merged into the Cookie header.

    Merged *by name, newest wins*. Naive concatenation wedges the client for
    good once a cached clearance expires: the expired `spchal-auth`/`sp_pow` are
    still on the request when the fresh challenge issues its own, Anubis reads
    the stale crumb of each duplicated pair, and the replay is challenged again
    — which caches another stale pair, and so on until the worker restarts.
    """
    headers = _headers_of(kwargs)
    previous = next((v for k, v in headers.items() if k.lower() == "cookie"), "")
    jar: dict[str, str] = {}
    for crumb in f"{previous}; {cookie}".split(";"):
        name, sep, value = crumb.strip().partition("=")
        if sep and name:
            jar[name] = value
    headers = {k: v for k, v in headers.items() if k.lower() != "cookie"}
    headers["cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
    return {**kwargs, "headers": headers}
