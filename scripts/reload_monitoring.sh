#!/usr/bin/env bash
set -euo pipefail

PROM_URL="${PROMETHEUS_URL:-http://localhost:9090}"
ALERT_URL="${ALERTMANAGER_URL:-http://localhost:9093}"

echo "Reloading Prometheus config at ${PROM_URL} ..."
curl -fsS -X POST "${PROM_URL}/-/reload" >/dev/null

echo "Reloading Alertmanager config at ${ALERT_URL} ..."
curl -fsS -X POST "${ALERT_URL}/-/reload" >/dev/null

echo "Monitoring configs reloaded successfully."