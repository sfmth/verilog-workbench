#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="verilog-workbench-ubuntu"
CONTAINER_NAME="verilog-workbench"
WORKDIR="/home/docker/verilog-workbench"
RECREATE=0

if [[ "${1:-}" == "--recreate" ]]; then
  RECREATE=1
elif [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: $0 [--recreate]"
  echo
  echo "  --recreate  Remove and recreate the persistent container."
  exit 0
elif [[ $# -gt 0 ]]; then
  echo "Unknown argument: $1" >&2
  echo "Usage: $0 [--recreate]" >&2
  exit 2
fi

DOCKER_ARGS=(
  -it
  --name "${CONTAINER_NAME}"
  --hostname "$(hostname)"
  -v "${SCRIPT_DIR}:${WORKDIR}"
  -w "${WORKDIR}"
)
XAUTH_FILE=""

if [[ -n "${DISPLAY:-}" && -d /tmp/.X11-unix ]]; then
  DOCKER_ARGS+=(
    -e "DISPLAY=${DISPLAY}"
    -e "QT_X11_NO_MITSHM=1"
    -v "/tmp/.X11-unix:/tmp/.X11-unix:rw"
  )

  if command -v xauth >/dev/null 2>&1; then
    mkdir -p "${SCRIPT_DIR}/.docker"
    XAUTH_FILE="${SCRIPT_DIR}/.docker/.xauth"
    XAUTH_DATA="$(xauth nlist "${DISPLAY}" 2>/dev/null || true)"

    if [[ -n "${XAUTH_DATA}" ]]; then
      touch "${XAUTH_FILE}"
      chmod 600 "${XAUTH_FILE}"
      printf '%s\n' "${XAUTH_DATA}" | sed -e 's/^..../ffff/' | xauth -f "${XAUTH_FILE}" nmerge -

      DOCKER_ARGS+=(
        -e "XAUTHORITY=/tmp/.docker.xauth"
        -v "${XAUTH_FILE}:/tmp/.docker.xauth:ro"
      )
    else
      XAUTH_FILE=""
    fi
  fi

  if [[ -z "${XAUTH_FILE}" && -f "${XAUTHORITY:-${HOME}/.Xauthority}" ]]; then
    XAUTH_FILE="${XAUTHORITY:-${HOME}/.Xauthority}"
    DOCKER_ARGS+=(
      -e "XAUTHORITY=/tmp/.docker.xauth"
      -v "${XAUTH_FILE}:/tmp/.docker.xauth:ro"
    )
  elif command -v xhost >/dev/null 2>&1; then
    xhost +SI:localuser:"$(id -un)" >/dev/null
    trap 'xhost -SI:localuser:"$(id -un)" >/dev/null 2>&1 || true' EXIT
  fi
fi

if [[ -d /dev/dri ]]; then
  DOCKER_ARGS+=(--device /dev/dri)
fi

docker build \
  --build-arg USER_UID="$(id -u)" \
  --build-arg USER_GID="$(id -g)" \
  -t "${IMAGE_NAME}" \
  "${SCRIPT_DIR}"

if docker container inspect "${CONTAINER_NAME}" >/dev/null 2>&1; then
  if [[ "${RECREATE}" == "1" ]]; then
    docker rm -f "${CONTAINER_NAME}" >/dev/null
    docker run "${DOCKER_ARGS[@]}" "${IMAGE_NAME}"
    exit 0
  fi

  CURRENT_X11_MOUNT="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/tmp/.X11-unix"}}{{.Source}}{{end}}{{end}}' "${CONTAINER_NAME}")"
  CURRENT_XAUTH_MOUNT="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/tmp/.docker.xauth"}}{{.Source}}{{end}}{{end}}' "${CONTAINER_NAME}")"

  if [[ -n "${DISPLAY:-}" && "${CURRENT_X11_MOUNT}" != "/tmp/.X11-unix" ]]; then
    echo "Existing container is missing the X11 display mount." >&2
    echo "Run './run-docker.sh --recreate' once to recreate it with display support." >&2
    exit 1
  fi

  if [[ -n "${XAUTH_FILE}" && "${CURRENT_XAUTH_MOUNT}" != "${XAUTH_FILE}" ]]; then
    echo "Existing container has stale Xauthority settings." >&2
    echo "Run './run-docker.sh --recreate' once to recreate it with the current display auth." >&2
    exit 1
  fi

  if [[ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" == "true" ]]; then
    docker attach "${CONTAINER_NAME}"
  else
    docker start -ai "${CONTAINER_NAME}"
  fi
else
  docker run "${DOCKER_ARGS[@]}" "${IMAGE_NAME}"
fi
