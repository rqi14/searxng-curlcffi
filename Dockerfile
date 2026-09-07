# SearXNG with the one thing stock SearXNG still lacks: an Anubis proof-of-work
# solver, which clears the PoW gate Startpage (and a growing number of sites)
# puts in front of results.
#
# Browser TLS/JA3 impersonation used to live here too; upstream made it native
# on 2026-09-03 (`[mod] network: migrate to curl_cffi`), so that half is gone.
# See patch_client.py and anubis.py for the measurements.
FROM searxng/searxng:latest

USER root
ENV VENV_PY=/usr/local/searxng/.venv/bin/python

# No pip step: curl_cffi is a base-image dependency now. If that ever changes,
# this import fails the build instead of the patch failing silently at runtime.
RUN $VENV_PY -c "import curl_cffi; print('curl_cffi', curl_cffi.__version__)"

COPY anubis.py anubis_session.py /usr/local/searxng/searx/network/
COPY patch_client.py /tmp/patch_client.py
RUN $VENV_PY /tmp/patch_client.py && rm /tmp/patch_client.py

# The image ships pre-compiled bytecode; editing the .py alone is NOT enough —
# the stale .pyc keeps being loaded, so the patch is present in the source, has
# no effect at runtime, and raises nothing. Drop the caches and recompile.
RUN find /usr/local/searxng/searx -name __pycache__ -type d -prune -exec rm -rf {} + \
 && $VENV_PY -m compileall -q /usr/local/searxng/searx

# This guard exists because the build failed silently once before: a patched .py
# that never executed.
COPY selfcheck.py /tmp/selfcheck.py
# Script mode puts sys.path[0] at /tmp, so searx would not import; pin it.
RUN PYTHONPATH=/usr/local/searxng $VENV_PY /tmp/selfcheck.py && rm /tmp/selfcheck.py

USER searxng
