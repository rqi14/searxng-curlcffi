#!/usr/bin/env python3
"""Build-time patch: hook the Anubis solver into SearXNG's curl_cffi client.

Up to 2026-09-03 this file also swapped httpx for curl_cffi so outgoing requests
carried a real browser's TLS/JA3 fingerprint. Upstream then did that itself
(`[mod] network: migrate to curl_cffi`), so all that is left to add is the
proof-of-work gate — measured 2026-09-07 against stock searxng/searxng:latest:

    engine      stock upstream (chrome impersonate)
    startpage   200 + 22 KB Anubis shell → SearxEngineCaptchaException
    google      200 ok

`AsyncClient` in searx/network/client.py is the single session class every
outgoing engine request is issued from, so wrapping its `request` is enough.
"""

from pathlib import Path
import sys

CLIENT = Path("/usr/local/searxng/searx/network/client.py")

# The class we monkeypatch. If upstream renames or drops it, fail the build
# rather than ship an image whose patch silently does nothing.
ANCHOR = "class AsyncClient(AsyncSession):"

HOOK = '''

# --- searxng-curlcffi -----------------------------------------------------
import os as _os  # noqa: E402  pylint: disable=wrong-import-position

# 1. Drop curl_cffi's browser default headers for DuckDuckGo. `new_client`
#    above hardcodes default_headers=True whenever impersonate is on, which
#    contradicts duckduckgo_web: that engine forces a Firefox 154 UA, curl_cffi
#    adds Chrome's sec-ch-ua client hints beside it, and DDG answers the d.js
#    call with `202 {}` — `KeyError: 'results'` in the parser. Measured
#    2026-09-07 via mihomo:
#
#        impersonate  default_headers  links.duckduckgo.com/d.js
#        chrome       True             202 {}       <- upstream default
#        chrome       False            200, 11 results
#        firefox      True             202 {}
#        none         False            200, 11 results
#
# ponytail: a host list, not a rule, because the two obvious rules were both
#    measured wrong. "Always off" leaves SearXNG with no User-Agent at all
#    (checked against an echo endpoint: only Host and Accept-Encoding go out) —
#    those default headers are its only source of one now, and google, startpage
#    and wikipedia immediately answer CAPTCHA/403. "Off whenever the engine set
#    its own UA" catches google too, which sets a Nokia UA to get the no-JS page
#    and still needs the rest of the headers — it CAPTCHAs without them. Add a
#    host here only after measuring it; SEARXNG_DEFAULT_HEADERS=1 disables the
#    whole thing.
_NO_DEFAULT_HEADERS = frozenset({"duckduckgo.com", "links.duckduckgo.com"})

_ORIGINAL_REQUEST = AsyncClient.request


async def _request(self, method: str, url: str, **kwargs):
    from urllib.parse import urlsplit as _urlsplit  # noqa: E402

    if _urlsplit(str(url)).hostname in _NO_DEFAULT_HEADERS:
        kwargs.setdefault("default_headers", False)
    return await _ORIGINAL_REQUEST(self, method, url, **kwargs)


if _os.environ.get("SEARXNG_DEFAULT_HEADERS", "0") != "1":
    AsyncClient.request = _request

# 2. Anubis proof-of-work solver. SEARXNG_ANUBIS=0 skips it.
if _os.environ.get("SEARXNG_ANUBIS", "1") != "0":
    from searx.network.anubis_session import install as _install_anubis  # noqa: E402

    _install_anubis(AsyncClient)
# --- end searxng-curlcffi -------------------------------------------------
'''


def main() -> int:
    src = CLIENT.read_text(encoding="utf-8")
    if "searxng-curlcffi" in src:
        print("already patched")
        return 0
    if ANCHOR not in src:
        print(f"ERROR: {ANCHOR!r} not found — upstream changed", file=sys.stderr)
        print("       inspect searx/network/client.py and update patch_client.py", file=sys.stderr)
        return 1
    CLIENT.write_text(src + HOOK, encoding="utf-8")
    print("patched", CLIENT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
