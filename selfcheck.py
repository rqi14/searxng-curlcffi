"""Build-time guards. Any failure here should fail the image, not surface later
as a silent `parsing error` on every search."""

import hashlib

import httpx

from searx.network.client import get_transport
from searx.network.anubis import extract_challenge, solve

transport = get_transport(True, True, "", "http://127.0.0.1:1", httpx.Limits(max_connections=10), 0)
assert type(transport).__name__ == "AnubisCurlTransport", f"got {type(transport).__name__}"
print("transport ok:", type(transport).__name__)

digest, nonce, elapsed_ms = solve("probe", 3)
assert digest == hashlib.sha256(f"probe{nonce}".encode()).hexdigest()
assert digest.startswith("000")
print(f"anubis solver ok: {digest[:10]} nonce={nonce} {elapsed_ms}ms")

assert extract_challenge("<html>plain page</html>") is None
assert extract_challenge('<html>/.within.website/x/cmd/anubis/ but no json</html>') is None
print("challenge detection ok")
