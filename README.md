# searxng-curlcffi

SearXNG with two additions aimed at engines that reject plain HTTP clients:

1. **curl_cffi transport** — outgoing engine requests carry a real browser's
   TLS/JA3 and HTTP2 fingerprint instead of httpx's.
2. **Anubis proof-of-work solver** — clears the PoW gate
   [Anubis](https://github.com/TecharoHQ/anubis) puts in front of results.
   Startpage runs it; adoption is growing.

Neither touches engine parsers: both live in the transport, so every engine
benefits and nothing has to learn what a challenge page looks like.

## Why

Measured 2026-08-29 from a UK residential exit, stock SearXNG vs this image:

| engine | stock | this image |
|---|---|---|
| google | ok | ok |
| bing | ok | ok |
| **startpage** | **CAPTCHA → 0 results** | **10 results** |
| brave | HTTP 429 | HTTP 429 (rate limit, not fingerprint) |
| duckduckgo (html) | 202 + anomaly | unchanged — the gate is not fingerprint |
| **duckduckgo web** | — | **10 results** — different endpoint, see below |

A full query went from 20 results (google+bing) to 32 (google+startpage+bing+mwmbl)
with zero unresponsive engines.

**DuckDuckGo is out of reach this way — but it is not out of reach.** Its
`html.` and `lite.` endpoints do gate *every* client behind a human challenge
regardless of fingerprint: re-measured 2026-09-04 across chrome, firefox133,
safari18_0, chrome99_android and `impersonate=none` (plain curl), from both a
datacentre and a UK residential exit, all seven identical — and the *same bytes*
that returned 11 results one hour returned `202` + `challenge-form` the next. It
is a server-side gate that varies over time, not a fingerprint check, so no
amount of impersonation moves it.

The fix is a different endpoint, not a better disguise. Upstream's
**`duckduckgo_web`** engine (added 2026-06-01, `disabled: true` by default)
scrapes `duckduckgo.com` for a `links.duckduckgo.com/d.js?…&dp=<token>` URL and
calls that JSON API — the same family as the images engine, which never had a
problem. No JS execution, no browser. Enable `duckduckgo web`, leave the
`duckduckgo` (html) engine off: 10 results, no errors.

An earlier revision of this file claimed DDG's main site "renders results with
JS. Only a real browser gets DDG results." That was wrong on 2026-08-29 when it
was written — `duckduckgo_web` had already been upstream for three months, and
its commit message says *"as alternative to html.duckduckgo.com"* in as many
words. The fingerprint axis had been measured to death; nobody checked how many
DDG engine implementations shipped in the box. A "cannot be done" note is the
most expensive kind to get wrong: it stops the next person looking.

## How the Anubis part works

Anubis serves a ~22 KB JS shell containing a challenge instead of the page.
Difficulty 4 means "find a nonce where `sha256(randomData + nonce)` starts with
4 hex zeros" — ~90 ms single-threaded here. The transport:

1. spots the challenge in an HTML response,
2. solves it,
3. redeems it at `/.within.website/x/cmd/anubis/api/pass-challenge`,
   **carrying the cookies the challenge page set** (they bind the solution to
   the issued challenge — redeeming without them returns HTTP 500),
4. replays the original request with the clearance cookie, cached per host so a
   cleared site costs one PoW rather than one per search.

`MAX_DIFFICULTY = 5` caps the grind: difficulty is exponential, and burning a
search's whole budget on a deliberate wall is worse than failing fast.

## Configuration

| env | default | meaning |
|---|---|---|
| `SEARXNG_IMPERSONATE` | `chrome` | curl_cffi profile (`firefox`, `safari17_0`, …). Empty string falls back to stock httpx. |

## Build

```bash
docker build -t searxng-curlcffi .
```

The build fails rather than shipping a broken image if the packages are missing,
the transport is not the patched one, or the PoW solver is wrong — see
`selfcheck.py`. Two earlier iterations of this image failed *silently*: a `.py`
patched while a stale `.pyc` kept being loaded, and a pip step swallowed by a
trailing `|| true`. Hence the guards.

## Upstream drift

`patch_transport.py` anchors on the body of `get_transport()` in
`searx/network/client.py` and refuses to patch if it has changed, so a
SearXNG update that moves that code fails the build loudly instead of producing
an image that quietly behaves like stock.
