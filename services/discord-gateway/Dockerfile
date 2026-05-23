FROM python:3.13-slim-trixie AS base

ENV DEBIAN_FRONTEND=noninteractive
ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PATH=/opt/venv/bin:${PATH}
ENV PYTHONPATH=/opt/venv/

RUN echo 'tzdata tzdata/Areas select Etc' | debconf-set-selections && \
    echo 'tzdata tzdata/Zones/Etc select UTC' | debconf-set-selections && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        wget \
        git \
        openssl \
        # jq for the API testing script
        jq \
        # vim and sudo for debugging — comment out once stable
        # vim \
        # sudo \
    # CVE-2026-27135: libnghttp2-14 DoS via malformed HTTP/2 frames after session
    # termination. Fixed in 1.64.0-1.1+deb13u1 (trixie security). Upgrade the
    # package explicitly so the fix is pulled regardless of base-image snapshot date.
    && apt-get install -y --only-upgrade libnghttp2-14 \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create a virtual environment
RUN python -m venv /opt/venv

WORKDIR /app

# Upgrade pip toolchain
RUN /opt/venv/bin/pip install --upgrade --no-cache-dir \
    pip \
    setuptools \
    wheel

# Copy and install requirements
COPY services/discord-gateway/requirements.txt ./requirements.txt

RUN /opt/venv/bin/pip install --upgrade --no-cache-dir --compile --prefer-binary \
    -r requirements.txt

# Copy shared libs
COPY ./services/shared /app/src/shared

# Catalog installed packages (useful for debugging)
RUN /opt/venv/bin/pip freeze

# Copy app code
COPY services/discord-gateway/ .

# Create a non-root user
RUN groupadd --gid 1002 botuser && \
    useradd --uid 1001 --gid botuser --shell /bin/bash --create-home botuser && \
    chown -R botuser /home/botuser && \
    chown -R botuser /app && \
    chmod -R 1777 /home/botuser && \
    chmod -R 1777 /app

USER botuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fs http://localhost:7999/api/v1/health || exit 1

# The '& tail -f /dev/null' keeps the container running if the app crashes,
# allowing exec access for debugging.
ENTRYPOINT ["/bin/bash", "-c", "source /opt/venv/bin/activate && /opt/venv/bin/python /app/src/bot.py & tail -f /dev/null"]
