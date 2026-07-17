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
        geeqie \
        git \
        graphviz \
        gtkwave \
        iverilog \
        librsvg2-bin \
        libpython3-dev \
        make \
        nextpnr-gowin \
        nextpnr-ice40 \
        nodejs \
        npm \
        openfpgaloader \
        python3 \
        python3-pip \
        verilator \
        yosys \
    && sudo rm -rf /var/lib/apt/lists/*
RUN pip install --break-system-packages cocotb==1.7.2 apycula click bitstring numpy pillow
RUN sudo npm install -g netlistsvg
ARG SBY_REV=fea6e467d067b3ea84b6b5ac08cd48beb59f0d42
RUN git clone https://github.com/YosysHQ/sby.git /tmp/sby \
    && git -C /tmp/sby checkout --detach ${SBY_REV} \
    && sudo make -C /tmp/sby install \
    && rm -rf /tmp/sby
RUN printf '\nalias vwb_examples="./vwb.py init --root . --src-dir examples/src --test-dir examples/test --build-dir .vwb"\n' >> "$HOME/.bashrc" 

COPY --chown=${USER_UID}:${USER_GID} . .

CMD ["/bin/bash"]
