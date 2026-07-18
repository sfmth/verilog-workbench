FROM ubuntu:26.04

ARG USERNAME=docker
ARG USER_UID=1000
ARG USER_GID=1000
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        boolector \
        ca-certificates \
        dbus-x11 \
        libgl1 \
        libgtk-3-0 \
        libice6 \
        libsm6 \
        libx11-6 \
        libxext6 \
        libxi6 \
        libxrender1 \
        libxtst6 \
        mesa-utils \
        sudo \
        x11-apps \
        xauth \
        z3 \
    && rm -rf /var/lib/apt/lists/* \
    && if ! getent group ${USER_GID} >/dev/null; then \
        if getent group ${USERNAME} >/dev/null; then groupmod --gid ${USER_GID} ${USERNAME}; else groupadd --gid ${USER_GID} ${USERNAME}; fi; \
    fi \
    && if ! getent passwd ${USER_UID} >/dev/null; then useradd --uid ${USER_UID} --gid ${USER_GID} --create-home --home-dir /home/docker --shell /bin/bash ${USERNAME}; fi \
    && mkdir -p /home/docker/verilog-workbench \
    && chown -R ${USER_UID}:${USER_GID} /home/docker \
    && USER_NAME="$(getent passwd ${USER_UID} | cut -d: -f1)" \
    && echo "${USER_NAME} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${USERNAME} \
    && chmod 0440 /etc/sudoers.d/${USERNAME}

ENV HOME=/home/docker
ENV PATH="/home/docker/.local/bin:${PATH}"
USER ${USER_UID}:${USER_GID}
WORKDIR /home/docker/verilog-workbench

RUN sudo apt-get update \
    && sudo apt-get install -y --no-install-recommends \
        fpga-icestorm \
        ghdl \
        geeqie \
        git \
        graphviz \
        gtkwave \
        inkscape \
        iverilog \
        libgvplugin-neato-layout8 \
        librsvg2-bin \
        libpython3-dev \
        make \
        nextpnr-gowin \
        nextpnr-ice40 \
        nodejs \
        npm \
        openfpgaloader \
        curl \
        python3 \
        python3-pip \
        unzip \
        verilator \
        yosys \
    && sudo rm -rf /var/lib/apt/lists/*
RUN pip install --break-system-packages argcomplete==3.6.3 cocotb==1.7.2 apycula click bitstring numpy pillow

ARG SV2V_VERSION=v0.0.13
ARG SV2V_REV=e5effb5e1ea4e0cf9b4af2c769d364e0ed4b6d84
ARG SV2V_AMD64_SHA256=552799a1d76cd177b9b4cc63a3e77823a3d2a6eb4ec006569288abeff28e1ff8
RUN set -eux; \
    architecture="$(dpkg --print-architecture)"; \
    if [ "${architecture}" = "amd64" ]; then \
        archive=/tmp/sv2v.zip; \
        curl -L --fail --show-error --silent \
            -o "${archive}" \
            "https://github.com/zachjs/sv2v/releases/download/${SV2V_VERSION}/sv2v-Linux.zip"; \
        echo "${SV2V_AMD64_SHA256}  ${archive}" | sha256sum -c -; \
        unzip -p "${archive}" sv2v-Linux/sv2v > /tmp/sv2v; \
        chmod 0755 /tmp/sv2v; \
    elif [ "${architecture}" = "arm64" ]; then \
        sudo apt-get update; \
        sudo apt-get install -y --no-install-recommends haskell-stack; \
        git clone https://github.com/zachjs/sv2v.git /tmp/sv2v-src; \
        git -C /tmp/sv2v-src checkout --detach "${SV2V_REV}"; \
        make -C /tmp/sv2v-src; \
        cp /tmp/sv2v-src/bin/sv2v /tmp/sv2v; \
        sudo apt-get purge -y --auto-remove haskell-stack; \
        sudo rm -rf /var/lib/apt/lists/* "${HOME}/.stack" /tmp/sv2v-src; \
    else \
        echo "unsupported sv2v architecture: ${architecture}" >&2; \
        exit 1; \
    fi; \
    sudo install -m 0755 /tmp/sv2v /usr/local/bin/sv2v; \
    rm -f /tmp/sv2v /tmp/sv2v.zip; \
    sv2v --version

ARG VERIBLE_VERSION=v0.0-4080-ga0a8d8eb
ARG VERIBLE_AMD64_SHA256=f75daa70f29dbe9624ffee3738408341cfdadbdaf7e5d714a5bcceb9223953e6
ARG VERIBLE_ARM64_SHA256=f573e073251032eff8245a7ff04ec88f08a624105748f986b822af43dbaeddce
RUN set -eux; \
    architecture="$(dpkg --print-architecture)"; \
    case "${architecture}" in \
        amd64) verible_arch=x86_64; checksum="${VERIBLE_AMD64_SHA256}" ;; \
        arm64) verible_arch=arm64; checksum="${VERIBLE_ARM64_SHA256}" ;; \
        *) echo "unsupported Verible architecture: ${architecture}" >&2; exit 1 ;; \
    esac; \
    archive=/tmp/verible.tar.gz; \
    curl -L --fail --show-error --silent \
        -o "${archive}" \
        "https://github.com/chipsalliance/verible/releases/download/${VERIBLE_VERSION}/verible-${VERIBLE_VERSION}-linux-static-${verible_arch}.tar.gz"; \
    echo "${checksum}  ${archive}" | sha256sum -c -; \
    mkdir /tmp/verible; \
    tar -xzf "${archive}" --strip-components=1 -C /tmp/verible; \
    sudo install -m 0755 /tmp/verible/bin/verible-* /usr/local/bin/; \
    rm -rf "${archive}" /tmp/verible; \
    verible-verilog-lint --version
RUN sudo npm install -g netlistsvg
ARG SBY_REV=fea6e467d067b3ea84b6b5ac08cd48beb59f0d42
RUN git clone https://github.com/YosysHQ/sby.git /tmp/sby \
    && git -C /tmp/sby checkout --detach ${SBY_REV} \
    && sudo make -C /tmp/sby install \
    && rm -rf /tmp/sby
COPY --chown=${USER_UID}:${USER_GID} . .

RUN mkdir -p "$HOME/.local/bin" \
    && ln -sf /home/docker/verilog-workbench/vwb.py "$HOME/.local/bin/vwb" \
    && printf '%s\n' \
        '' \
        '# Verilog Workbench command and terminal Tab completion.' \
        'if command -v register-python-argcomplete >/dev/null 2>&1; then' \
        '    eval "$(register-python-argcomplete --shell bash vwb ./vwb.py ../vwb.py)"' \
        'fi' \
        'alias vwb_examples="vwb init --root . --src-dir examples/src --test-dir examples/test --build-dir .vwb"' \
        >> "$HOME/.bashrc"

CMD ["/bin/bash"]
