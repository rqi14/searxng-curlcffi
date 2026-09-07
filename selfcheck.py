"""Build-time guards. Any failure here should fail the image, not surface later
as a silent `parsing error` on every search."""

import asyncio
import hashlib

from curl_cffi.requests.headers import Headers

import searx.network.client as client
from searx.network.client import AsyncClient
from searx.network.anubis import extract_challenge, solve
from searx.network.anubis_session import _cookie_header, _with_cookie

assert getattr(AsyncClient.request, "_anubis", False), "AsyncClient.request is not hooked"
print("client hook ok")

# A request carrying its own User-Agent must drop curl_cffi's browser default
# headers (their sec-ch-ua hints would contradict it); one without must keep
# them, since they are SearXNG's only source of a User-Agent.
seen: dict = {}


async def _spy(self, method, url, **kwargs):
    seen.clear()
    seen.update(kwargs)


client._ORIGINAL_REQUEST = _spy  # pylint: disable=protected-access
asyncio.run(client._request(None, "GET", "https://x/", headers={"User-Agent": "ff"}))
assert seen.get("default_headers") is False, seen
asyncio.run(client._request(None, "GET", "https://x/"))
assert "default_headers" not in seen, seen
print("default_headers narrowing ok")

digest, nonce, elapsed_ms = solve("probe", 3)
assert digest == hashlib.sha256(f"probe{nonce}".encode()).hexdigest()
assert digest.startswith("000")
print(f"anubis solver ok: {digest[:10]} nonce={nonce} {elapsed_ms}ms")

merged = _with_cookie({"headers": {"Cookie": "sp_pow=old; keep=1"}}, "sp_pow=new; spchal-auth=jwt")
merged = merged["headers"]["cookie"]
assert "sp_pow=old" not in merged and "sp_pow=new" in merged, merged
assert "keep=1" in merged and "spchal-auth=jwt" in merged, merged
print("cookie merge ok:", merged)


class _FakeResponse:
    headers = Headers([("set-cookie", "a=1; Path=/"), ("set-cookie", "b=2; Secure")])


assert _cookie_header(_FakeResponse) == "a=1; b=2", _cookie_header(_FakeResponse)
print("set-cookie fold ok")

assert extract_challenge("<html>plain page</html>") is None
assert extract_challenge('<html>/.within.website/x/cmd/anubis/ but no json</html>') is None
print("challenge detection ok")
