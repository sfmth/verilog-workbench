#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMAGE_NAME="verilog-workbench-ubuntu"
CONTAINER_NAME="verilog-workbench"
WORKDIR="/home/docker/verilog-workbench"
RECREATE=0
USB_BUS="/dev/bus/usb"
USB_CGROUP_RULE="c 189:* rwm"
USB_ENABLED=0
HOST_DEVICE_GROUPS=()

if [[ "${1:-}" == "--recreate" ]]; then
  RECREATE=1
elif [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  echo "Usage: $0 [--recreate]"
  echo
  echo "  --recreate  Remove and recreate the persistent container."
  echo
  echo "Linux USB devices are forwarded automatically for FPGA programming."
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

if [[ "$(uname -s)" == "Linux" ]]; then
  if [[ -d "${USB_BUS}" ]]; then
    USB_ENABLED=1
    DOCKER_ARGS+=(
      -v "${USB_BUS}:${USB_BUS}"
      --device-cgroup-rule "${USB_CGROUP_RULE}"
    )

    PRIMARY_GID="$(id -g)"
    read -r -a HOST_GROUP_IDS <<< "$(id -G)"
    for GROUP_ID in "${HOST_GROUP_IDS[@]}"; do
      if [[ "${GROUP_ID}" != "${PRIMARY_GID}" ]]; then
        HOST_DEVICE_GROUPS+=("${GROUP_ID}")
        DOCKER_ARGS+=(--group-add "${GROUP_ID}")
      fi
    done
  else
    echo "Note: ${USB_BUS} is unavailable; USB FPGA programmers cannot be forwarded." >&2
  fi
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
  CURRENT_USB_MOUNT="$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/dev/bus/usb"}}{{.Source}}{{end}}{{end}}' "${CONTAINER_NAME}")"
  CURRENT_USB_RULES="$(docker inspect -f '{{range .HostConfig.DeviceCgroupRules}}{{println .}}{{end}}' "${CONTAINER_NAME}")"
  CURRENT_EXTRA_GROUPS="$(docker inspect -f '{{range .HostConfig.GroupAdd}}{{println .}}{{end}}' "${CONTAINER_NAME}")"

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

  if [[ "${USB_ENABLED}" == "1" ]]; then
    USB_SETTINGS_STALE=0
    if [[ "${CURRENT_USB_MOUNT}" != "${USB_BUS}" ]] || ! grep -Fxq "${USB_CGROUP_RULE}" <<< "${CURRENT_USB_RULES}"; then
      USB_SETTINGS_STALE=1
    fi
    for GROUP_ID in "${HOST_DEVICE_GROUPS[@]}"; do
      if ! grep -Fxq "${GROUP_ID}" <<< "${CURRENT_EXTRA_GROUPS}"; then
        USB_SETTINGS_STALE=1
        break
      fi
    done
    if [[ "${USB_SETTINGS_STALE}" == "1" ]]; then
      echo "Existing container is missing the current USB device access settings." >&2
      echo "Run './run-docker.sh --recreate' once to enable FPGA programmer access." >&2
      exit 1
    fi
  fi

  if [[ "$(docker inspect -f '{{.State.Running}}' "${CONTAINER_NAME}")" == "true" ]]; then
    docker attach "${CONTAINER_NAME}"
  else
    docker start -ai "${CONTAINER_NAME}"
  fi
else
  docker run "${DOCKER_ARGS[@]}" "${IMAGE_NAME}"
fi
