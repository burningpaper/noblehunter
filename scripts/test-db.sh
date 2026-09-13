#!/usr/bin/env bash
# Throwaway Postgres for the test suite. Bound to localhost only; the password is
# a local-test placeholder, not a real secret.
#
#   scripts/test-db.sh up     start (or reuse) the container and wait until ready
#   scripts/test-db.sh down   remove the container
set -euo pipefail

NAME="noble-test-db"
PORT="55432"
IMAGE="postgres:17"

case "${1:-}" in
  up)
    if ! docker info >/dev/null 2>&1; then
      echo "Docker isn't running. Start Docker Desktop and try again." >&2
      exit 1
    fi
    if [ -z "$(docker ps -q -f name=^${NAME}$)" ]; then
      docker rm -f "$NAME" >/dev/null 2>&1 || true
      docker run -d --name "$NAME" \
        -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=noble_test \
        -p "127.0.0.1:${PORT}:5432" "$IMAGE" >/dev/null
    fi
    for _ in $(seq 1 60); do
      if docker exec "$NAME" pg_isready -U postgres -d noble_test >/dev/null 2>&1; then
        echo "Test Postgres ready on 127.0.0.1:${PORT}"
        exit 0
      fi
      sleep 1
    done
    echo "Test Postgres did not become ready within 60s" >&2
    exit 1
    ;;
  down)
    docker rm -f "$NAME" >/dev/null 2>&1 && echo "Test Postgres removed" || echo "No test Postgres running"
    ;;
  *)
    echo "Usage: $0 up|down" >&2
    exit 2
    ;;
esac
