"""Build-time guards. Any failure here should fail the image, not surface later
as a silent `parsing error` on every search."""

import hashlib

import httpx

from searx.network.client import get_transport
from searx.network.anubis import extract_challenge, solve
from searx.network.anubis_transport import _with_cookie

transport = get_transport(True, True, "", "http://127.0.0.1:1", httpx.Limits(max_connections=10), 0)
assert type(transport).__name__ == "AnubisCurlTransport", f"got {type(transport).__name__}"
print("transport ok:", type(transport).__name__)

digest, nonce, elapsed_ms = solve("probe", 3)
assert digest == hashlib.sha256(f"probe{nonce}".encode()).hexdigest()
assert digest.startswith("000")
print(f"anubis solver ok: {digest[:10]} nonce={nonce} {elapsed_ms}ms")

stale = httpx.Request("GET", "https://x/", headers={"cookie": "sp_pow=old; keep=1"})
merged = _with_cookie(stale, "sp_pow=new; spchal-auth=jwt").headers["cookie"]
assert "sp_pow=old" not in merged and "sp_pow=new" in merged, merged
assert "keep=1" in merged and "spchal-auth=jwt" in merged, merged
print("cookie merge ok:", merged)

assert extract_challenge("<html>plain page</html>") is None
assert extract_challenge('<html>/.within.website/x/cmd/anubis/ but no json</html>') is None
print("challenge detection ok")
