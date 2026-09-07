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

# --- searxng-curlcffi: Anubis proof-of-work solver ------------------------
# Set SEARXNG_ANUBIS=0 to run stock upstream behaviour.
import os as _os  # noqa: E402  pylint: disable=wrong-import-position

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
