#!/bin/sh
# docker-entrypoint.sh
#
# Role-aware container entrypoint for the SOW-to-Jira image.
#
# Selects the process to run based on the SOW_ROLE environment variable so the
# same image can serve as a Render web service or (eventually) a background
# worker. Defaults to the production web server, mirroring the historical
# Dockerfile CMD, so behaviour is unchanged when SOW_ROLE is unset.
#
#   SOW_ROLE=web     (default) -> gunicorn + uvicorn worker on 0.0.0.0:${PORT:-8000}
#   SOW_ROLE=worker           -> arq background worker (walled; placeholder for now)
#
# `exec` replaces the shell with the target process so signals (SIGTERM from
# Render/Docker) propagate directly for clean shutdown.

set -e

SOW_ROLE="${SOW_ROLE:-web}"
PORT="${PORT:-8000}"

case "$SOW_ROLE" in
    web)
        echo "[entrypoint] role=web -> starting gunicorn (uvicorn worker) on 0.0.0.0:${PORT}"
        exec gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b "0.0.0.0:${PORT}" ui.server:app
        ;;
    worker)
        # The real arq worker is walled (not yet wired). Fail loudly rather than
        # silently degrading, so a misconfigured Render worker service is obvious.
        echo "[entrypoint] role=worker not yet wired (walled) -- no worker command available" >&2
        exit 1
        ;;
    *)
        echo "[entrypoint] unknown SOW_ROLE='${SOW_ROLE}' (expected 'web' or 'worker')" >&2
        exit 1
        ;;
esac
