#!/usr/bin/env python3
"""Build-time patch: swap SearXNG's httpx transport for a curl_cffi one.

SearXNG issues plain httpx requests. Several engines reject those on TLS/JA3
fingerprint alone — 2026-08-29 measurements from a UK residential exit:

    engine      plain httpx        curl_cffi (impersonate=chrome)
    google      200 ok             200 ok
    startpage   200 + CAPTCHA      200 ok  (22 KB of real results)
    brave       429                429     (rate limit, not fingerprint)
    duckduckgo  202 + anomaly      202 + anomaly

Fingerprinting alone got Startpage past its bot wall but not to results: it
runs Anubis, a proof-of-work gate, and served a JS shell that the engine
parser choked on. searx/network/anubis.py solves that PoW (difficulty 4 in
~90 ms) and anubis_transport.py redeems it transparently, so the engine sees
the page it always expected. DuckDuckGo stays unfixable this way: its
html/lite endpoints gate every client behind a human challenge, and its main
site needs real JS execution.

Injection point is `get_transport()` in searx/network/client.py — the single
place every outgoing engine request's transport is built.
"""

from pathlib import Path
import sys

CLIENT = Path("/usr/local/searxng/searx/network/client.py")

ANCHOR = '''    return httpx.AsyncHTTPTransport(
        # pylint: disable=protected-access
        verify=_verify,
        http2=http2,
        limits=limit,
        proxy=httpx._config.Proxy(proxy_url) if proxy_url else None,  # pyright: ignore[reportPrivateUsage]
        local_address=local_address,
        retries=retries,
    )'''

REPLACEMENT = '''    # --- searxng-curlcffi: browser TLS/JA3 + HTTP2 fingerprint -------------
    # Impersonation profile is env-tunable (SEARXNG_IMPERSONATE, e.g. chrome,
    # firefox, chrome131, safari17_0). Set it empty to fall back to stock httpx.
    import os as _os

    _imp = _os.environ.get("SEARXNG_IMPERSONATE", "chrome").strip()
    if _imp:
        try:
            from searx.network.anubis_transport import AnubisCurlTransport as _CurlTransport
            from curl_cffi.const import CurlHttpVersion as _HttpVer

            return _CurlTransport(
                impersonate=_imp,
                verify=verify,
                proxy=proxy_url or None,
                local_address=local_address or None,
                http_version=_HttpVer.V2_0 if http2 else _HttpVer.V1_1,
                max_connections=limit.max_connections or 10,
            )
        except Exception as exc:  # pragma: no cover - fall back to stock httpx
            import logging as _logging

            _logging.getLogger("searx.network.client").warning(
                "curl_cffi transport unavailable (%s), using stock httpx", exc
            )
    # --- end searxng-curlcffi ----------------------------------------------
    return httpx.AsyncHTTPTransport(
        # pylint: disable=protected-access
        verify=_verify,
        http2=http2,
        limits=limit,
        proxy=httpx._config.Proxy(proxy_url) if proxy_url else None,  # pyright: ignore[reportPrivateUsage]
        local_address=local_address,
        retries=retries,
    )'''


def main() -> int:
    src = CLIENT.read_text(encoding="utf-8")
    if "searxng-curlcffi" in src:
        print("already patched")
        return 0
    if ANCHOR not in src:
        print("ERROR: get_transport() anchor not found — upstream changed", file=sys.stderr)
        print("       inspect searx/network/client.py and update patch_transport.py", file=sys.stderr)
        return 1
    CLIENT.write_text(src.replace(ANCHOR, REPLACEMENT, 1), encoding="utf-8")
    print("patched", CLIENT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
