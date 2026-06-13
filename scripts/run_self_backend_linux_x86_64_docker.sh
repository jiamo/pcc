#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
IMAGE_TAG="${PCC_SELF_BACKEND_LINUX_X86_64_IMAGE:-pcc-self-backend-linux-x86_64:latest}"
DOCKERFILE="${REPO_ROOT}/docker/self-backend-linux-x86_64.Dockerfile"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is required for the Linux x86_64 self-backend harness" >&2
  exit 127
fi

if [[ ! -f "${DOCKERFILE}" ]]; then
  echo "missing Dockerfile: ${DOCKERFILE}" >&2
  exit 1
fi

if [[ "${PCC_SELF_BACKEND_DOCKER_REBUILD:-0}" == "1" ]] || ! docker image inspect "${IMAGE_TAG}" >/dev/null 2>&1; then
  docker build \
    --platform linux/amd64 \
    -f "${DOCKERFILE}" \
    -t "${IMAGE_TAG}" \
    "${REPO_ROOT}"
fi

docker run --rm \
  --platform linux/amd64 \
  -e UV_PROJECT_ENVIRONMENT=/tmp/pcc-linux-x86_64-venv \
  -e UV_LINK_MODE=copy \
  -e PCC_BUILD_SKIP=1 \
  -v "${REPO_ROOT}:/workspace" \
  -w /workspace \
  "${IMAGE_TAG}" \
  "$@"
