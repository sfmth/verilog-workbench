#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN="${HOME}/.local/bin"
TEMP_DIR="$(mktemp -d -t vwb-setup-XXXXXX)"
trap 'rm -rf "${TEMP_DIR}"' EXIT

SV2V_VERSION="v0.0.13"
SV2V_AMD64_SHA256="552799a1d76cd177b9b4cc63a3e77823a3d2a6eb4ec006569288abeff28e1ff8"
VERIBLE_VERSION="v0.0-4080-ga0a8d8eb"
VERIBLE_AMD64_SHA256="f75daa70f29dbe9624ffee3738408341cfdadbdaf7e5d714a5bcceb9223953e6"
VERIBLE_ARM64_SHA256="f573e073251032eff8245a7ff04ec88f08a624105748f986b822af43dbaeddce"
SBY_REV="fea6e467d067b3ea84b6b5ac08cd48beb59f0d42"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    printf '%s\n' \
        "Usage: ./setup.sh" \
        "" \
        "Install Verilog Workbench and its tools on Ubuntu or Debian." \
        "Core tools are required; optional tools are installed when available."
    exit 0
fi
if [[ $# -ne 0 ]]; then
    printf 'Unknown option: %s\n' "$1" >&2
    exit 2
fi

if ! command -v apt-get >/dev/null 2>&1; then
    printf '%s\n' \
        "setup.sh supports Ubuntu and Debian." \
        "On another system, use Docker with ./run-docker.sh." >&2
    exit 2
fi

if [[ ${EUID} -eq 0 ]]; then
    SUDO=()
elif command -v sudo >/dev/null 2>&1; then
    SUDO=(sudo)
else
    printf '%s\n' "sudo is required to install system packages." >&2
    exit 2
fi

note() {
    printf '\n==> %s\n' "$1"
}

warn() {
    printf 'warning: %s\n' "$1" >&2
}

note "Installing the core simulator and Python tools"
"${SUDO[@]}" apt-get update
"${SUDO[@]}" apt-get install -y --no-install-recommends \
    ca-certificates curl git iverilog make python3 python3-pip unzip

optional_packages=(
    boolector fpga-icestorm ghdl geeqie graphviz gtkwave inkscape
    libgvplugin-neato-layout8 libpython3-dev librsvg2-bin nextpnr-gowin
    nextpnr-ice40 nodejs npm openfpgaloader verilator yosys z3
)
available_packages=()
for package in "${optional_packages[@]}"; do
    if apt-cache show "$package" >/dev/null 2>&1; then
        available_packages+=("$package")
    else
        warn "optional package is unavailable: $package"
    fi
done
if [[ ${#available_packages[@]} -gt 0 ]]; then
    note "Installing available waveform, lint, synthesis, formal, and FPGA tools"
    if ! "${SUDO[@]}" apt-get install -y --no-install-recommends \
        "${available_packages[@]}"; then
        warn "some optional system packages could not be installed"
    fi
fi

pip_options=(--user)
if python3 -m pip install --help 2>/dev/null | grep -q -- --break-system-packages; then
    pip_options+=(--break-system-packages)
fi
python3 -m pip install "${pip_options[@]}" \
    argcomplete==3.6.3 cocotb==1.7.2
python3 -m pip install "${pip_options[@]}" \
    apycula click bitstring numpy pillow || \
    warn "optional Python packages for examples and FPGA builds could not be installed"

mkdir -p "$LOCAL_BIN"
ln -sf "$REPO_ROOT/vwb.py" "$LOCAL_BIN/vwb"
export PATH="$LOCAL_BIN:$PATH"

shell_block=$(cat <<'EOF'

# Verilog Workbench command and terminal Tab completion.
export PATH="$HOME/.local/bin:$PATH"
if command -v register-python-argcomplete >/dev/null 2>&1; then
    eval "$(register-python-argcomplete --shell bash vwb)"
fi
EOF
)
if [[ ! -f "$HOME/.bashrc" ]] || \
    ! grep -q "# Verilog Workbench command" "$HOME/.bashrc"; then
    printf '%s\n' "$shell_block" >> "$HOME/.bashrc"
fi

install_sv2v() {
    local archive="$TEMP_DIR/sv2v.zip"
    [[ "$(dpkg --print-architecture)" == "amd64" ]] || return 1
    curl -L --fail --show-error --silent -o "$archive" \
        "https://github.com/zachjs/sv2v/releases/download/${SV2V_VERSION}/sv2v-Linux.zip" \
        || return 1
    printf '%s  %s\n' "$SV2V_AMD64_SHA256" "$archive" | \
        sha256sum -c - || return 1
    unzip -p "$archive" sv2v-Linux/sv2v > "$TEMP_DIR/sv2v" || return 1
    chmod 0755 "$TEMP_DIR/sv2v"
    "${SUDO[@]}" install -m 0755 "$TEMP_DIR/sv2v" /usr/local/bin/sv2v
}

install_verible() {
    local architecture verible_arch checksum archive
    architecture="$(dpkg --print-architecture)"
    case "$architecture" in
        amd64)
            verible_arch="x86_64"
            checksum="$VERIBLE_AMD64_SHA256"
            ;;
        arm64)
            verible_arch="arm64"
            checksum="$VERIBLE_ARM64_SHA256"
            ;;
        *) return 1 ;;
    esac
    archive="$TEMP_DIR/verible.tar.gz"
    curl -L --fail --show-error --silent -o "$archive" \
        "https://github.com/chipsalliance/verible/releases/download/${VERIBLE_VERSION}/verible-${VERIBLE_VERSION}-linux-static-${verible_arch}.tar.gz" \
        || return 1
    printf '%s  %s\n' "$checksum" "$archive" | sha256sum -c - || return 1
    mkdir -p "$TEMP_DIR/verible"
    tar -xzf "$archive" --strip-components=1 -C "$TEMP_DIR/verible" || return 1
    "${SUDO[@]}" install -m 0755 \
        "$TEMP_DIR"/verible/bin/verible-* /usr/local/bin/
}

install_sby() {
    git clone --quiet https://github.com/YosysHQ/sby.git "$TEMP_DIR/sby" || return 1
    git -C "$TEMP_DIR/sby" checkout --quiet --detach "$SBY_REV" || return 1
    "${SUDO[@]}" make -C "$TEMP_DIR/sby" install >/dev/null
}

note "Installing optional tools that are not packaged consistently"
if command -v npm >/dev/null 2>&1; then
    "${SUDO[@]}" npm install -g netlistsvg >/dev/null 2>&1 || \
        warn "NetlistSVG could not be installed; Graphviz schematics remain available"
fi
command -v sv2v >/dev/null 2>&1 || install_sv2v || warn \
    "sv2v could not be installed; many SystemVerilog designs still work directly"
command -v verible-verilog-lint >/dev/null 2>&1 || install_verible || \
    warn "Verible could not be installed; the other available linters still work"
command -v sby >/dev/null 2>&1 || install_sby || \
    warn "SymbiYosys could not be installed; only formal checks are affected"

note "Checking the installation"
(cd "$REPO_ROOT" && "$LOCAL_BIN/vwb" doctor) || true
printf '\nSetup complete. Start a new shell, or run: source ~/.bashrc\n'
