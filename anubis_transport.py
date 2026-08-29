"""httpx transport that impersonates a browser and clears Anubis PoW walls.

Layering, outermost first:

    AnubisCurlTransport      <- retries once after solving a PoW challenge
      AsyncCurlTransport     <- curl_cffi: real browser TLS/JA3 + HTTP2
        curl_cffi session    <- keeps the clearance cookie for later requests

Solving in the transport rather than in an engine parser means every engine
benefits, and no parser has to learn what a challenge page looks like.
"""

from __future__ import annotations

import logging

import httpx
from httpx_curl_cffi import AsyncCurlTransport

from searx.network.anubis import extract_challenge, pass_challenge_url

logger = logging.getLogger("searx.network.anubis")

# Only sniff bodies that could plausibly be a challenge page. Anubis serves a
# small self-contained HTML shell; skipping everything else keeps the fast path
# (real search results, often hundreds of KB) free of extra buffering.
MAX_SNIFF_BYTES = 256 * 1024

# httpx keeps cookies on the Client, not the transport, and the curl session
# does not carry them across our internal hops — so the clearance cookie has to
# be captured and replayed by hand. Cached per host so a cleared site costs one
# PoW, not one per search.
_CLEARANCE: dict[str, str] = {}


class AnubisCurlTransport(AsyncCurlTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in _CLEARANCE:
            request = _with_cookie(request, _CLEARANCE[host])
        response = await super().handle_async_request(request)

        if request.method not in ("GET", "POST"):
            return response
        if "html" not in response.headers.get("content-type", ""):
            return response

        body = await response.aread()
        if len(body) > MAX_SNIFF_BYTES:
            return _rebuild(response, body, request)

        blob = extract_challenge(body.decode("utf-8", "replace"))
        if blob is None:
            return _rebuild(response, body, request)

        redeem = pass_challenge_url(str(request.url), blob)
        if redeem is None:
            return _rebuild(response, body, request)

        # Redeem, then replay the original request. The clearance cookie lives
        # in the underlying curl session, so both hops carry it automatically.
        try:
            # Carry the original extensions across: httpx_curl_cffi asserts on
            # extensions["timeout"], which a freshly built Request does not have.
            # The challenge page sets cookies that bind the solution to the
            # issued challenge (sp_pow, spchal-cookie-verification on Startpage).
            # Redeeming without them is rejected with HTTP 500.
            redeem_headers = _forward_headers(request)
            issued = _clearance_cookie(response)
            if issued:
                redeem_headers["cookie"] = issued
            redeemed = await super().handle_async_request(
                httpx.Request(
                    "GET",
                    redeem,
                    headers=redeem_headers,
                    extensions=dict(request.extensions),
                )
            )
            await redeemed.aclose()
            granted = _clearance_cookie(redeemed)
            cookie = "; ".join(c for c in (issued, granted) if c)
            logger.debug(
                "anubis: redeem status=%s location=%s cookie=%s",
                redeemed.status_code,
                redeemed.headers.get("location", "")[:80],
                cookie[:120] or "(none)",
            )
            if cookie:
                _CLEARANCE[host] = cookie
                request = _with_cookie(request, cookie)
            retried = await super().handle_async_request(request)
        except Exception as exc:  # pragma: no cover - network fault, serve the wall
            logger.warning(
                "anubis: redeem failed (%s: %s), returning challenge page",
                type(exc).__name__, exc, exc_info=True,
            )
            return _rebuild(response, body, request)

        retried_body = await retried.aread()
        if extract_challenge(retried_body.decode("utf-8", "replace")) is not None:
            logger.warning("anubis: still challenged after redeem for %s", request.url.host)
        else:
            logger.info("anubis: cleared %s", request.url.host)
        return _rebuild(retried, retried_body, request)


def _forward_headers(request: httpx.Request) -> dict[str, str]:
    """Carry UA/language across so the redeem looks like the same client."""
    keep = ("user-agent", "accept-language", "accept")
    return {k: v for k, v in request.headers.items() if k.lower() in keep}


# Reading the body decodes it, so the transfer headers describing the *encoded*
# form must not be replayed — httpx would try to gunzip already-plain bytes and
# raise DecodingError (seen as `unexpected crash` on every startpage search).
_STRIP_HEADERS = (b"content-encoding", b"content-length", b"transfer-encoding")


def _rebuild(response: httpx.Response, body: bytes, request: httpx.Request) -> httpx.Response:
    """Re-wrap an already-read response so httpx can still consume it."""
    headers = [(k, v) for k, v in response.headers.raw if k.lower() not in _STRIP_HEADERS]
    return httpx.Response(
        status_code=response.status_code,
        headers=headers,
        content=body,
        request=request,
        extensions=response.extensions,
    )


def _clearance_cookie(response: httpx.Response) -> str:
    """Fold the Set-Cookie headers of a redeem response into one Cookie value."""
    pairs = []
    for key, value in response.headers.raw:
        if key.lower() != b"set-cookie":
            continue
        crumb = value.decode("latin-1").split(";", 1)[0].strip()
        if "=" in crumb:
            pairs.append(crumb)
    return "; ".join(pairs)


def _with_cookie(request: httpx.Request, cookie: str) -> httpx.Request:
    """Clone a request with the clearance cookie merged into its Cookie header."""
    headers = httpx.Headers(request.headers)
    existing = headers.get("cookie")
    headers["cookie"] = f"{existing}; {cookie}" if existing else cookie
    return httpx.Request(
        request.method,
        request.url,
        headers=headers,
        content=request.read(),
        extensions=dict(request.extensions),
    )
