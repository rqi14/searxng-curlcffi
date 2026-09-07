# searxng-curlcffi

SearXNG with an **Anubis proof-of-work solver** — it clears the PoW gate
[Anubis](https://github.com/TecharoHQ/anubis) puts in front of results.
Startpage runs it; adoption is growing.

It does not touch engine parsers: the solver wraps the HTTP client, so every
engine benefits and nothing has to learn what a challenge page looks like.

> **2026-09-07 — the image lost half its job, on purpose.** This repo also used
> to patch in a curl_cffi transport so outgoing requests carried a real browser's
> TLS/JA3 fingerprint instead of httpx's. Upstream SearXNG did exactly that
> itself on 2026-09-03 (`[mod] network: migrate to curl_cffi`): httpx is gone,
> `impersonate` defaults to `chrome`, and `get_transport()` no longer exists —
> which is what started failing the nightly build. That half is now deleted, and
> `SEARXNG_IMPERSONATE` with it; use upstream's own `impersonate` setting.

## Why

Measured 2026-09-07 against stock `searxng/searxng:latest`, which already
impersonates chrome:

| engine | stock upstream | this image |
|---|---|---|
| google | ok | ok |
| **startpage** | **22 KB Anubis shell → `SearxEngineCaptchaException`, 0 results** | **10 results** |

Chrome fingerprinting gets past Startpage's bot wall and lands on the PoW shell.
That is the whole remaining gap.

**DuckDuckGo is out of reach this way — but it is not out of reach.** Its
`html.` and `lite.` endpoints gate *every* client behind a human challenge
regardless of fingerprint: measured 2026-09-04 across chrome, firefox133,
safari18_0, chrome99_android and `impersonate=none` (plain curl), from both a
datacentre and a UK residential exit, all seven identical — and the *same bytes*
that returned 11 results one hour returned `202` + `challenge-form` the next. It
is a server-side gate that varies over time, not a fingerprint check.

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

## How it works

Anubis serves a ~22 KB JS shell containing a challenge instead of the page.
Difficulty 4 means "find a nonce where `sha256(randomData + nonce)` starts with
4 hex zeros" — ~90 ms single-threaded here. `anubis_session.py` wraps
`AsyncClient.request`, the one place every non-streaming engine request is
issued from, and:

1. spots the challenge in an HTML response,
2. solves it,
3. redeems it at `/.within.website/x/cmd/anubis/api/pass-challenge`,
   **carrying the cookies the challenge page set** (they bind the solution to
   the issued challenge — redeeming without them returns HTTP 500) and without
   following the redirect, since the clearance arrives as `Set-Cookie` on the 3xx,
4. replays the original request with the clearance cookie, cached per host so a
   cleared site costs one PoW rather than one per search.

Upstream builds its session with `discard_cookies=True`, so the clearance cookie
is never kept for us — hence the hand-rolled per-host cache. Cookies are merged
*by name, newest wins*; naive concatenation wedges the client for good once a
cached clearance expires.

`MAX_DIFFICULTY = 5` caps the grind: difficulty is exponential, and burning a
search's whole budget on a deliberate wall is worse than failing fast.

## Configuration

| env | default | meaning |
|---|---|---|
| `SEARXNG_ANUBIS` | `1` | Set to `0` to skip the hook and run stock upstream behaviour. |

Impersonation is upstream's now — see `impersonate` in SearXNG's `settings.yml`.

## Build

```bash
docker build -t searxng-curlcffi .
```

The build fails rather than shipping a broken image if `curl_cffi` is missing,
the client is not hooked, or the PoW solver is wrong — see `selfcheck.py`. Two
earlier iterations of this image failed *silently*: a `.py` patched while a stale
`.pyc` kept being loaded, and a pip step swallowed by a trailing `|| true`.
Hence the guards.

## Upstream drift

`patch_client.py` anchors on `class AsyncClient(AsyncSession):` in
`searx/network/client.py` and refuses to patch if it is gone, so a SearXNG
update that moves that code fails the build loudly instead of producing an image
that quietly behaves like stock. That is exactly what caught the curl_cffi
migration — the nightly build broke the day the anchor moved, rather than three
weeks later via a mysteriously empty result page.
