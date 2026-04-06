#!/usr/bin/env bash
set -euo pipefail

SYSTEM_TEST_ENV_FILE="${SYSTEM_TEST_ENV_FILE:-.env.remote}"

if [ -f "${SYSTEM_TEST_ENV_FILE}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${SYSTEM_TEST_ENV_FILE}"
  set +a
fi

SYSTEM_TEST_HOST="${SYSTEM_TEST_HOST:-}"
SYSTEM_TEST_USER="${SYSTEM_TEST_USER:-}"
SYSTEM_TEST_PATH="${SYSTEM_TEST_PATH:-}"
SYSTEM_TEST_PORT="${SYSTEM_TEST_PORT:-22}"
SYSTEM_TEST_REF="${SYSTEM_TEST_REF:-main}"
SYSTEM_TEST_PREPARE="${SYSTEM_TEST_PREPARE:-bash scripts/deploy-server.sh ${SYSTEM_TEST_REF}}"
SYSTEM_TEST_COMMAND="${SYSTEM_TEST_COMMAND:-}"

if [ -z "${SYSTEM_TEST_HOST}" ]; then
  echo "SYSTEM_TEST_HOST is required"
  exit 1
fi

if [ -z "${SYSTEM_TEST_USER}" ]; then
  echo "SYSTEM_TEST_USER is required"
  exit 1
fi

if [ -z "${SYSTEM_TEST_PATH}" ]; then
  echo "SYSTEM_TEST_PATH is required"
  exit 1
fi

if [ -z "${SYSTEM_TEST_COMMAND}" ]; then
  echo "SYSTEM_TEST_COMMAND is required"
  echo "Example:"
  echo "  SYSTEM_TEST_COMMAND='docker compose --env-file .env exec -T bridge python -m pytest -q tests/system'"
  exit 1
fi

echo "Running remote system tests on ${SYSTEM_TEST_USER}@${SYSTEM_TEST_HOST}:${SYSTEM_TEST_PATH}"
echo "Ref: ${SYSTEM_TEST_REF}"
echo "Prepare: ${SYSTEM_TEST_PREPARE}"
echo "Command: ${SYSTEM_TEST_COMMAND}"

ssh -p "${SYSTEM_TEST_PORT}" "${SYSTEM_TEST_USER}@${SYSTEM_TEST_HOST}" \
  "cd '${SYSTEM_TEST_PATH}' && ${SYSTEM_TEST_PREPARE} && ${SYSTEM_TEST_COMMAND}"
