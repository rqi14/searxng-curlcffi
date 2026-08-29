"""Transparent Anubis proof-of-work solver for SearXNG's outgoing requests.

Anubis (https://github.com/TecharoHQ/anubis) gates a page behind a SHA-256
proof of work instead of a CAPTCHA. Startpage runs it; more sites keep
adopting it. A plain HTTP client sees a ~22 KB JS shell with an embedded
challenge and no results, which SearXNG's engine parsers then fail on
(`json.decoder.JSONDecodeError` for startpage).

The work is trivial for a machine: difficulty 4 means "sha256(randomData +
nonce) must start with 4 hex zeros", solved in ~90 ms single-threaded on this
box. So rather than teach every engine parser about the shell, solve the
challenge in the transport and hand the engine the page it expected.

Protocol (read off the shipped main.mjs, v1.26.4):
  1. Page embeds  {"rules": {"algorithm", "difficulty"}, "challenge": {...}}
  2. Client finds nonce with sha256(challenge.randomData + nonce) starting
     with `difficulty` zeros.
  3. GET {origin}/.within.website/x/cmd/anubis/api/pass-challenge
         ?id=&response=&nonce=&redir=&elapsedTime=
     which sets the clearance cookie and redirects back to `redir`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from urllib.parse import urlencode, urlsplit, urlunsplit

logger = logging.getLogger("searx.network.anubis")

CHALLENGE_RE = re.compile(r'(\{"rules":\{.*?\}\})\s*</script>', re.S)
PASS_PATH = "/.within.website/x/cmd/anubis/api/pass-challenge"

# Refuse to grind forever: difficulty is exponential (16**d expected hashes).
# 5 is already ~1M hashes (~1 s here); anything above that is a deliberate wall
# and burning a search request's whole budget on it is worse than failing fast.
MAX_DIFFICULTY = 5


def extract_challenge(body: str) -> dict | None:
    """Return the parsed Anubis challenge blob, or None if this isn't one."""
    if "/.within.website/x/cmd/anubis/" not in body:
        return None
    match = CHALLENGE_RE.search(body)
    if not match:
        return None
    try:
        blob = json.loads(match.group(1))
    except ValueError:
        return None
    if "challenge" not in blob or "rules" not in blob:
        return None
    return blob


def solve(random_data: str, difficulty: int) -> tuple[str, int, int]:
    """Return (hash_hex, nonce, elapsed_ms) for the PoW."""
    target = "0" * difficulty
    started = time.monotonic()
    nonce = 0
    while True:
        digest = hashlib.sha256(f"{random_data}{nonce}".encode()).hexdigest()
        if digest.startswith(target):
            return digest, nonce, int((time.monotonic() - started) * 1000)
        nonce += 1


def pass_challenge_url(request_url: str, blob: dict) -> str | None:
    """Build the URL that redeems a solved challenge, or None if unsolvable."""
    challenge = blob["challenge"]
    difficulty = int(blob["rules"].get("difficulty") or challenge.get("difficulty") or 0)
    if not difficulty or difficulty > MAX_DIFFICULTY:
        logger.warning("anubis: refusing difficulty %s (max %s)", difficulty, MAX_DIFFICULTY)
        return None

    digest, nonce, elapsed = solve(challenge["randomData"], difficulty)
    logger.info("anubis: solved difficulty %d in %d ms (nonce=%d)", difficulty, elapsed, nonce)

    parts = urlsplit(request_url)
    query = urlencode(
        {
            "id": challenge["id"],
            "response": digest,
            "nonce": nonce,
            "redir": request_url,
            "elapsedTime": str(elapsed),
        }
    )
    return urlunsplit((parts.scheme, parts.netloc, PASS_PATH, query, ""))
