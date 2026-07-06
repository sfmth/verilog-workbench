FROM ubuntu:latest

ARG USERNAME=docker
ARG USER_UID=1000
ARG USER_GID=1000
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
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
USER ${USER_UID}:${USER_GID}
WORKDIR /home/docker/verilog-workbench

CMD ["/bin/bash"]

RUN sudo apt update -y && sudo apt upgrade -y && sudo apt install -y iverilog yosys gtkwave verilator imagemagick nodejs npm geeqie git make python3 python3-pip libpython3-dev nextpnr-ice40 nextpnr-gowin fpga-icestorm openfpgaloader
RUN pip install --break-system-packages cocotb==1.7.2 apycula && export PATH=$PATH:~/.local/bin/
RUN sudo npm install -g netlistsvg
RUN git clone https://github.com/sfmth/verilog-workbench/
