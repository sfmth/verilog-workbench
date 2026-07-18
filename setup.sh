#!/usr/bin/env bash

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_BIN="${HOME}/.local/bin"
VENV_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/verilog-workbench/venv"
PROFILE=core
DRY_RUN=false
USE_AUR=true

usage() {
    printf '%s\n' \
        "Usage: ./setup.sh [--full] [--no-aur] [--dry-run]" \
        "" \
        "Install Verilog Workbench on Ubuntu, Debian, Fedora, or Arch Linux." \
        "" \
        "The default core install provides Python, Icarus Verilog, Cocotb," \
        "the vwb command, and terminal Tab completion. This is enough to list," \
        "test, and record waves for Verilog and many SystemVerilog designs." \
        "" \
        "Options:" \
        "  --full      Also install available VHDL, lint, synthesis, viewer," \
        "              formal, and FPGA tools." \
        "  --no-aur    On Arch Linux, do not ask paru or yay for AUR packages." \
        "  --dry-run   Show the detected system and planned commands only." \
        "  -h, --help  Show this help."
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --full) PROFILE=full ;;
        --no-aur) USE_AUR=false ;;
        --dry-run) DRY_RUN=true ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf 'Unknown option: %s\n\n' "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

note() {
    printf '\n==> %s\n' "$1"
}

warn() {
    printf 'warning: %s\n' "$1" >&2
}

die() {
    printf 'error: %s\n' "$1" >&2
    exit 2
}

run_command() {
    if [[ "$DRY_RUN" == true ]]; then
        printf 'DRY RUN:'
        printf ' %q' "$@"
        printf '\n'
        return 0
    fi
    "$@"
}

OS_RELEASE_FILE="${VWB_SETUP_OS_RELEASE:-/etc/os-release}"
if [[ -r "$OS_RELEASE_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$OS_RELEASE_FILE"
fi
DISTRO_ID="${VWB_SETUP_DISTRO:-${ID:-unknown}}"
DISTRO_VERSION="${VWB_SETUP_VERSION:-${VERSION_ID:-unknown}}"
if [[ -n "${VWB_SETUP_DISTRO:-}" ]]; then
    DISTRO_LIKE=""
else
    DISTRO_LIKE="${ID_LIKE:-}"
fi

case " ${DISTRO_ID} ${DISTRO_LIKE} " in
    *" arch "*|*" manjaro "*|*" endeavouros "*)
        DISTRO_FAMILY=arch
        PACKAGE_MANAGER=pacman
        ;;
    *" fedora "*|*" rhel "*)
        DISTRO_FAMILY=fedora
        PACKAGE_MANAGER=dnf
        ;;
    *" ubuntu "*|*" debian "*)
        DISTRO_FAMILY=debian
        PACKAGE_MANAGER=apt
        ;;
    *)
        die "unsupported Linux distribution '${DISTRO_ID}'. Use ./run-docker.sh, or install the tools reported by vwb doctor."
        ;;
esac

note "Detected ${DISTRO_ID} ${DISTRO_VERSION} (${PACKAGE_MANAGER}, ${PROFILE} profile)"

if [[ "$DRY_RUN" != true ]] && ! command -v "$PACKAGE_MANAGER" >/dev/null 2>&1; then
    die "${PACKAGE_MANAGER} was not found even though ${DISTRO_ID} was detected"
fi

if [[ ${EUID} -eq 0 ]]; then
    SUDO=()
elif command -v sudo >/dev/null 2>&1; then
    SUDO=(sudo)
elif [[ "$DRY_RUN" == true ]]; then
    SUDO=(sudo)
else
    die "sudo is required to install system packages"
fi

package_available() {
    local package="$1"
    if [[ "$DRY_RUN" == true ]]; then
        return 0
    fi
    case "$DISTRO_FAMILY" in
        debian) apt-cache show --no-all-versions "$package" >/dev/null 2>&1 ;;
        fedora) dnf -q list "$package" >/dev/null 2>&1 ;;
        arch) pacman -Si "$package" >/dev/null 2>&1 ;;
    esac
}

append_package() {
    local package="$1" existing
    for existing in "${PACKAGES[@]:-}"; do
        [[ "$existing" == "$package" ]] && return 0
    done
    PACKAGES+=("$package")
}

select_package() {
    local purpose="$1" importance="$2" package
    shift 2
    for package in "$@"; do
        if package_available "$package"; then
            append_package "$package"
            return 0
        fi
    done
    if [[ "$importance" == required ]]; then
        die "no ${purpose} package was found in the enabled ${DISTRO_ID} repositories (tried: $*)"
    fi
    if [[ "$importance" == optional ]]; then
        warn "optional ${purpose} package is unavailable in the enabled repositories"
    fi
    return 1
}

add_optional_packages() {
    local package
    for package in "$@"; do
        if package_available "$package"; then
            append_package "$package"
        else
            warn "optional package is unavailable: ${package}"
        fi
    done
}

note "Reading package information"
case "$DISTRO_FAMILY" in
    debian) run_command "${SUDO[@]}" apt-get update ;;
    fedora) run_command "${SUDO[@]}" dnf -q makecache ;;
    arch) run_command "${SUDO[@]}" pacman -Sy --noconfirm ;;
esac

PACKAGES=()
case "$DISTRO_FAMILY" in
    debian)
        select_package "CA certificates" required ca-certificates
        select_package "Python" required python3
        select_package "Python virtual environment" required python3-venv
        select_package "Icarus Verilog" required iverilog
        select_package "Cocotb" fallback python3-cocotb || true
        select_package "Tab completion" fallback python3-argcomplete || true
        CORE_PACKAGE_COUNT=${#PACKAGES[@]}
        if [[ "$PROFILE" == full ]]; then
            add_optional_packages \
                boolector fpga-icestorm ghdl geeqie graphviz gtkwave inkscape \
                librsvg2-bin nextpnr-gowin nextpnr-ice40 nodejs npm \
                openfpgaloader python3-bitstring python3-numpy python3-pil \
                symbiyosys verilator verible yosys z3 sv2v
        fi
        ;;
    fedora)
        select_package "CA certificates" required ca-certificates
        select_package "Python" required python3
        select_package "Python package installer" required python3-pip
        select_package "Icarus Verilog" required iverilog
        select_package "Cocotb" fallback python3-cocotb python-cocotb || true
        select_package "Tab completion" fallback python3-argcomplete || true
        CORE_PACKAGE_COUNT=${#PACKAGES[@]}
        if [[ "$PROFILE" == full ]]; then
            add_optional_packages \
                boolector ghdl geeqie graphviz gtkwave inkscape librsvg2-tools \
                nextpnr nodejs npm openFPGALoader symbiyosys verilator \
                verible yosys z3 sv2v icestorm python3-bitstring python3-numpy \
                python3-pillow
        fi
        ;;
    arch)
        select_package "CA certificates" required ca-certificates
        select_package "Python" required python
        select_package "Python package installer" required python-pip
        select_package "Icarus Verilog" required iverilog
        select_package "Cocotb" fallback python-cocotb || true
        select_package "Tab completion" fallback python-argcomplete || true
        CORE_PACKAGE_COUNT=${#PACKAGES[@]}
        if [[ "$PROFILE" == full ]]; then
            add_optional_packages \
                boolector ghdl geeqie graphviz gtkwave inkscape librsvg \
                nextpnr nodejs npm openfpgaloader verilator yosys z3 icestorm \
                python-bitstring python-numpy python-pillow
        fi
        ;;
esac

CORE_PACKAGES=("${PACKAGES[@]:0:CORE_PACKAGE_COUNT}")
OPTIONAL_PACKAGES=("${PACKAGES[@]:CORE_PACKAGE_COUNT}")

note "Installing core packages from the ${DISTRO_ID} repositories"
case "$DISTRO_FAMILY" in
    debian)
        run_command "${SUDO[@]}" apt-get install -y --no-install-recommends \
            "${CORE_PACKAGES[@]}"
        ;;
    fedora)
        run_command "${SUDO[@]}" dnf install -y --setopt=install_weak_deps=False \
            "${CORE_PACKAGES[@]}"
        ;;
    arch)
        run_command "${SUDO[@]}" pacman -Syu --needed --noconfirm \
            "${CORE_PACKAGES[@]}"
        ;;
esac

if [[ ${#OPTIONAL_PACKAGES[@]} -gt 0 ]]; then
    note "Installing available optional packages"
    optional_install_failed=false
    case "$DISTRO_FAMILY" in
        debian)
            run_command "${SUDO[@]}" apt-get install -y --no-install-recommends \
                "${OPTIONAL_PACKAGES[@]}" || \
                optional_install_failed=true
            ;;
        fedora)
            run_command "${SUDO[@]}" dnf install -y \
                --setopt=install_weak_deps=False "${OPTIONAL_PACKAGES[@]}" || \
                optional_install_failed=true
            ;;
        arch)
            run_command "${SUDO[@]}" pacman -S --needed --noconfirm \
                "${OPTIONAL_PACKAGES[@]}" || \
                optional_install_failed=true
            ;;
    esac
    if [[ "$optional_install_failed" == true ]]; then
        warn "the optional package batch failed; retrying its packages separately"
        for package in "${OPTIONAL_PACKAGES[@]}"; do
            case "$DISTRO_FAMILY" in
                debian)
                    run_command "${SUDO[@]}" apt-get install -y \
                        --no-install-recommends "$package" || \
                        warn "optional package could not be installed: ${package}"
                    ;;
                fedora)
                    run_command "${SUDO[@]}" dnf install -y \
                        --setopt=install_weak_deps=False "$package" || \
                        warn "optional package could not be installed: ${package}"
                    ;;
                arch)
                    run_command "${SUDO[@]}" pacman -S --needed --noconfirm \
                        "$package" || \
                        warn "optional package could not be installed: ${package}"
                    ;;
            esac
        done
    fi
fi

export PATH="${VENV_DIR}/bin:${LOCAL_BIN}:${PATH}"

AUR_HELPER=""
if [[ "$DISTRO_FAMILY" == arch && "$USE_AUR" == true ]]; then
    if command -v paru >/dev/null 2>&1; then
        AUR_HELPER=paru
    elif command -v yay >/dev/null 2>&1; then
        AUR_HELPER=yay
    fi
fi

aur_install_candidates() {
    local purpose="$1" package
    shift
    [[ -n "$AUR_HELPER" ]] || return 1
    if [[ ${EUID} -eq 0 ]]; then
        warn "AUR packages are not built as root; skipping ${purpose}"
        return 1
    fi
    for package in "$@"; do
        note "Trying the AUR package ${package} for ${purpose}"
        if run_command "$AUR_HELPER" -S --needed --noconfirm "$package"; then
            return 0
        fi
    done
    return 1
}

if [[ "$DISTRO_FAMILY" == arch && "$USE_AUR" == true ]]; then
    if [[ -z "$AUR_HELPER" ]]; then
        warn "paru or yay was not found; unavailable Arch packages will use the Python fallback or remain optional"
    elif ! command -v cocotb-config >/dev/null 2>&1; then
        aur_install_candidates "Cocotb" python-cocotb || true
    fi
fi

PYTHON=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON=python3
elif command -v python >/dev/null 2>&1; then
    PYTHON=python
elif [[ "$DRY_RUN" == true ]]; then
    PYTHON=python3
else
    die "Python was installed but no python3 or python command is available"
fi

VENV_REQUIREMENTS=()
if [[ "$DRY_RUN" == true ]] || ! command -v cocotb-config >/dev/null 2>&1; then
    VENV_REQUIREMENTS+=("cocotb>=1.9,<3")
fi
if [[ "$DRY_RUN" == true ]] || ! command -v register-python-argcomplete >/dev/null 2>&1; then
    VENV_REQUIREMENTS+=("argcomplete>=3,<5")
fi
if [[ "$PROFILE" == full ]]; then
    for dependency in "bitstring:bitstring" "numpy:numpy" "PIL:pillow"; do
        module="${dependency%%:*}"
        package="${dependency#*:}"
        if [[ "$DRY_RUN" == true ]] || ! "$PYTHON" -c "import ${module}" >/dev/null 2>&1; then
            VENV_REQUIREMENTS+=("$package")
        fi
    done
fi
if [[ ${#VENV_REQUIREMENTS[@]} -gt 0 ]]; then
    note "Installing missing Python tools in an isolated user environment"
    run_command "$PYTHON" -m venv --system-site-packages "$VENV_DIR"
    run_command "$VENV_DIR/bin/python" -m pip install --upgrade \
        "${VENV_REQUIREMENTS[@]}"
fi

if [[ "$PROFILE" == full && "$DISTRO_FAMILY" == arch && -n "$AUR_HELPER" ]]; then
    command -v sv2v >/dev/null 2>&1 || \
        aur_install_candidates "SystemVerilog conversion" sv2v || true
    command -v verible-verilog-lint >/dev/null 2>&1 || \
        aur_install_candidates "Verible lint" verible verible-git || true
    command -v sby >/dev/null 2>&1 || \
        aur_install_candidates "formal checks" symbiyosys symbiyosys-git || true
    command -v netlistsvg >/dev/null 2>&1 || \
        aur_install_candidates "NetlistSVG schematics" netlistsvg netlistsvg-git || true
fi

if [[ "$PROFILE" == full ]] && ! command -v netlistsvg >/dev/null 2>&1 \
    && command -v npm >/dev/null 2>&1; then
    note "Installing NetlistSVG with the Node package manager"
    if ! run_command "${SUDO[@]}" npm install --global netlistsvg; then
        warn "NetlistSVG could not be installed; Graphviz schematics remain available"
    fi
fi

if [[ "$PROFILE" == full ]]; then
    declare -A OPTIONAL_COMMANDS=(
        [ghdl]="VHDL simulation"
        [gtkwave]="wave viewing"
        [yosys]="synthesis and lint"
        [verilator]="extra lint"
        [verible-verilog-lint]="extra SystemVerilog lint"
        [sv2v]="advanced SystemVerilog conversion"
        [netlistsvg]="NetlistSVG schematics"
        [sby]="formal checks"
    )
    for command in "${!OPTIONAL_COMMANDS[@]}"; do
        if ! command -v "$command" >/dev/null 2>&1; then
            warn "${OPTIONAL_COMMANDS[$command]} is unavailable; VWB will skip it or use another installed backend"
        fi
    done
fi

note "Installing the vwb command"
if [[ "$DRY_RUN" == true ]]; then
    run_command mkdir -p "$LOCAL_BIN"
    run_command ln -s "$REPO_ROOT/vwb.py" "$LOCAL_BIN/vwb"
else
    mkdir -p "$LOCAL_BIN"
    if [[ -e "$LOCAL_BIN/vwb" && ! -L "$LOCAL_BIN/vwb" ]]; then
        warn "${LOCAL_BIN}/vwb is a regular file; leaving it unchanged"
    else
        ln -sfn "$REPO_ROOT/vwb.py" "$LOCAL_BIN/vwb"
    fi
fi

append_shell_setup() {
    local file="$1" shell_name="$2" marker="# >>> Verilog Workbench setup >>>"
    local completion
    [[ -f "$file" ]] && grep -Fq "$marker" "$file" && return 0
    if [[ "$shell_name" == fish ]]; then
        completion='if command -q register-python-argcomplete; register-python-argcomplete --shell fish vwb | source; end'
    else
        completion="if command -v register-python-argcomplete >/dev/null 2>&1; then eval \"\$(register-python-argcomplete --shell ${shell_name} vwb)\"; fi"
    fi
    if [[ "$DRY_RUN" == true ]]; then
        printf 'DRY RUN: add vwb PATH and %s completion to %s\n' "$shell_name" "$file"
        return 0
    fi
    mkdir -p "$(dirname "$file")"
    if [[ "$shell_name" == fish ]]; then
        printf '\n%s\nfish_add_path "%s/bin" "%s"\n%s\n%s\n' \
            "$marker" "$VENV_DIR" "$LOCAL_BIN" "$completion" \
            '# <<< Verilog Workbench setup <<<' >> "$file"
    else
        printf '\n%s\nexport PATH="%s/bin:%s:$PATH"\n%s\n%s\n' \
            "$marker" "$VENV_DIR" "$LOCAL_BIN" "$completion" \
            '# <<< Verilog Workbench setup <<<' >> "$file"
    fi
}

LOGIN_SHELL="${SHELL:-/bin/bash}"
case "${LOGIN_SHELL##*/}" in
    zsh) append_shell_setup "$HOME/.zshrc" zsh ;;
    fish) append_shell_setup "$HOME/.config/fish/config.fish" fish ;;
    *) append_shell_setup "$HOME/.bashrc" bash ;;
esac

if [[ "$DRY_RUN" == true ]]; then
    note "Dry run complete; no files or packages were changed"
    exit 0
fi

command -v iverilog >/dev/null 2>&1 || die "Icarus Verilog is still missing after installation"
command -v cocotb-config >/dev/null 2>&1 || die "Cocotb is still missing after the package and virtual-environment attempts"

note "Checking the installation"
"$LOCAL_BIN/vwb" \
    --root "$REPO_ROOT" \
    --src-dir examples/src \
    --test-dir examples/test \
    doctor || true
printf '\nSetup complete. Start a new terminal, then run: vwb init\n'
