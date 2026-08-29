# SearXNG with two things stock SearXNG lacks, both aimed at engines that reject
# plain HTTP clients:
#
#   1. curl_cffi transport — outgoing requests carry a real browser's TLS/JA3 and
#      HTTP2 fingerprint instead of httpx's.
#   2. Anubis proof-of-work solver — clears the PoW gate Startpage (and a growing
#      number of sites) puts in front of results.
#
# See patch_transport.py and anubis.py for the measurements behind both.
FROM searxng/searxng:latest

# PyPI has to be reachable while building; declare these so --build-arg works.
ARG HTTP_PROXY=""
ARG HTTPS_PROXY=""
ARG NO_PROXY=""

USER root
ENV VENV_PY=/usr/local/searxng/.venv/bin/python

# The upstream venv is built by uv and ships no pip; ensurepip provides one.
# Do NOT chain `|| true` onto this — `A && B || true` swallows a failing install
# and yields an image that silently lacks the packages (learned the hard way).
RUN $VENV_PY -m ensurepip --upgrade
RUN $VENV_PY -m pip install --no-cache-dir --disable-pip-version-check curl_cffi httpx-curl_cffi
RUN $VENV_PY -c "import curl_cffi, httpx_curl_cffi; print('curl_cffi', curl_cffi.__version__)"

COPY anubis.py anubis_transport.py /usr/local/searxng/searx/network/
COPY patch_transport.py /tmp/patch_transport.py
RUN $VENV_PY /tmp/patch_transport.py && rm /tmp/patch_transport.py

# The image ships pre-compiled bytecode; editing the .py alone is NOT enough —
# the stale .pyc keeps being loaded, so the patch is present in the source, has
# no effect at runtime, and raises nothing. Drop the caches and recompile.
RUN find /usr/local/searxng/searx -name __pycache__ -type d -prune -exec rm -rf {} + \
 && $VENV_PY -m compileall -q /usr/local/searxng/searx

# Both guards below exist because this build failed silently twice before:
# a patched .py that never executed, and a pip step swallowed by `|| true`.
COPY selfcheck.py /tmp/selfcheck.py
# Script mode puts sys.path[0] at /tmp, so searx would not import; pin it.
RUN PYTHONPATH=/usr/local/searxng $VENV_PY /tmp/selfcheck.py && rm /tmp/selfcheck.py

USER searxng
