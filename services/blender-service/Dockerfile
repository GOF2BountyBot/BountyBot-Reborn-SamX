# Below should always be the latest LTS release
ARG UBUNTU_RELEASE=24.04
# Below should be a reasonably current Cuda version
ARG CUDA_VERSION=13.0.0
# Blender is installed from an official tarball (not apt) because:
#   - Ubuntu 24.04 apt repos only ship Blender 4.0.2, which lacks support for newer GPU
#     architectures (e.g. sm_120 / Blackwell / RTX 5090).
#   - Snap is not an option inside Docker (requires systemd).
# To update Blender, change the version string here.
#   Browse available releases: https://download.blender.org/release/
#   Prefer LTS releases for stability: https://www.blender.org/download/lts/
# NOTE: If you bump to a new minor or major version (e.g. 4.5 → 5.0), also update
#   the URL folder name in the tarball install RUN step below (e.g. Blender4.5 → Blender5.0).
ARG BLENDER_VERSION=4.5.7

# Might be able to change this to something a bit smaller (like runtime instead of devel) 
# from https://hub.docker.com/r/nvidia/cuda/tags
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-devel-ubuntu${UBUNTU_RELEASE} AS base

# Re-declare ARGs after FROM so they are available to RUN instructions in this stage
ARG BLENDER_VERSION=4.5.7

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONUNBUFFERED=1
ENV CUDA_HOME=/usr/local/cuda-13
ENV PATH=/opt/venv/bin:/usr/local/nvidia/bin:/usr/local/cuda/bin:${PATH}
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/opt/venv/

# Update cuda apt repo
RUN apt-key del 7fa2af80 && \
    sed -i '/developer\.download\.nvidia\.com\/compute\/cuda\/repos/d' /etc/apt/sources.list.d/* && \
    sed -i '/developer\.download\.nvidia\.com\/compute\/machine-learning\/repos/d' /etc/apt/sources.list.d/* && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        wget && \
    wget https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2404/x86_64/cuda-keyring_1.1-1_all.deb && \
    dpkg -i cuda-keyring_1.1-1_all.deb 

# Install system dependencies and Python
RUN echo 'tzdata tzdata/Areas select Etc' | debconf-set-selections; \
    echo 'tzdata tzdata/Zones/Etc select UTC' | debconf-set-selections; \
    apt-get update && \
    apt-get --with-new-pkgs upgrade -y && \
    apt-get install -y --no-install-recommends \
        software-properties-common \
        autoconf \
        apt-utils \
        pkg-config && \
    # Add deadsnakes ppa for added python versions
    add-apt-repository ppa:deadsnakes/ppa
        
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        wget \
        curl \
        git \
        python3-pip \
        python3-dev \
        python3-venv \
        libpython3-dev \
        openssl \
        # Extras for bountybot
        g++-14 \
        gcc-14 \
        # Temp install vim and sudo for debugging...
        vim \
        sudo \
        # Blender runtime dependencies (X11, Wayland, OpenGL) — the official
        # tarball dynamically links against these and will fail at startup without them.
        libwayland-client0 \
        libwayland-egl1 \
        libx11-6 \
        libxxf86vm1 \
        libxcursor1 \
        libxi6 \
        libxrandr2 \
        libxinerama1 \
        libxkbcommon-dev \
        libxkbcommon-x11-dev \
        libsm6 \
        libice6 \
        libgl1 \
        libglx0 \
        libopengl0 \
        libxt6t64 \
        # 7zip for extraction of assets on container startup...
        7zip && \
    apt-get autoremove -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Blender from the official tarball.
# We do NOT use apt (Ubuntu 24.04 ships only 4.0.2 which lacks sm_120/Blackwell support)
# and NOT snap (requires systemd, unavailable in Docker).
# The URL path component (Blender4.5) encodes the major.minor version — update it if you
# bump BLENDER_VERSION to a new minor/major (e.g. 4.5 → 5.0 means Blender4.5 → Blender5.0).
RUN BLENDER_MAJOR_MINOR=$(echo "$BLENDER_VERSION" | cut -d. -f1,2) && \
    TARBALL="blender-${BLENDER_VERSION}-linux-x64.tar.xz" && \
    wget -q "https://download.blender.org/release/Blender${BLENDER_MAJOR_MINOR}/${TARBALL}" -O "/tmp/${TARBALL}" && \
    mkdir -p /opt/blender && \
    tar -xf "/tmp/${TARBALL}" --strip-components=1 -C /opt/blender && \
    ln -s /opt/blender/blender /usr/local/bin/blender && \
    rm -f "/tmp/${TARBALL}" && \
    blender --version

RUN ln -s /usr/bin/python3 /usr/bin/python

# Create a virtual environment
RUN python3 -m venv /opt/venv

WORKDIR /app

# Copy requirements files
COPY services/blender-service/requirements.txt .
# Issue with requirements file copying atm, so nixing for now...
COPY services/blender-service/src/lib/AEPi/requirements.txt ./aepi.requirements.txt

# Activate the virtual environment and upgrade pip
RUN chmod +x /opt/venv/bin/activate && \
    /opt/venv/bin/pip install --upgrade --no-cache-dir \
        pip \
        setuptools \
        wheel  \
        ninja \
        meson
    
## Install dependencies
RUN /opt/venv/bin/pip install --upgrade --no-cache-dir --prefer-binary \
    -r requirements.txt \
    -r aepi.requirements.txt

# Install gdown for pulling assets from Google Drive
RUN /opt/venv/bin/pip install --upgrade --no-cache-dir --prefer-binary \
    gdown
    
# mainly for debugging - catalog all pip packages and versions
RUN /opt/venv/bin/pip freeze

## Copy remaining app code...
COPY services/blender-service/ .

# Copy shared libs
COPY ./services/shared /app/src/shared

# create a non-root user and update permissions...
RUN groupadd --gid 1002 botuser && \
    useradd --uid 1001 --gid botuser --shell /bin/bash --create-home botuser && \
    # sudo stuff - nuke eventually once stable to prevent non-priv user from being able to elevate...
    usermod -aG sudo botuser && \
    echo "botuser:botuser" | chpasswd && \
    echo "%sudo ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers && \
    chown -R botuser /home/botuser && \
    chown -R botuser /app && \
    chmod -R 1777 /home/botuser && \
    chmod -R 1777 /app

USER botuser

# The ' & tail -f /dev/null' in the entrypoint below is a hack to keep the container running if the app crashes.
# This can be handy for being able to connect to the container CLI and check files/logs since it will still be running.
#
# Temp disable startup script and run python directly.
# This allows faster deploy times when doing dev/debugging on other services 
# that don't require actual blender service functionality 
# (namely the asset downloads and warmup) - the API will still start and run.
# 
# To re-enable the startup script, uncomment the first line and comment
# the second line below. 

ENTRYPOINT ["/bin/bash", "-c", "source /opt/venv/bin/activate  && /app/docker-entrypoint.sh & tail -f /dev/null"]
#ENTRYPOINT ["/bin/bash", "-c", "source /opt/venv/bin/activate && /opt/venv/bin/python /app/src/main.py & tail -f /dev/null"]
