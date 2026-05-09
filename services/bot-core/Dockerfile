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
        # PostgreSQL client for migration tooling
        postgresql-client \
        # 7zip for extraction of assets on container startup
        7zip \
        # vim and sudo for debugging — comment out once stable
        # vim \
        # sudo \
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
COPY services/bot-core/requirements.txt ./requirements.txt

RUN /opt/venv/bin/pip install --upgrade --no-cache-dir --prefer-binary \
    -r requirements.txt

# Install gdown for pulling assets from Google Drive
RUN /opt/venv/bin/pip install --upgrade --no-cache-dir --prefer-binary \
    gdown

# Catalog installed packages (useful for debugging)
RUN /opt/venv/bin/pip freeze

# Copy app code
COPY services/bot-core/ .

# Copy shared libs
COPY ./services/shared /app/src/shared

# Create a non-root user
RUN groupadd --gid 1002 botuser && \
    useradd --uid 1001 --gid botuser --shell /bin/bash --create-home botuser && \
    chown -R botuser /home/botuser && \
    chown -R botuser /app && \
    chmod -R 1777 /home/botuser && \
    chmod -R 1777 /app

USER botuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl -s -o /dev/null -f http://localhost:8000/api/v1/health/ || exit 1

# The '& tail -f /dev/null' keeps the container running if the app crashes,
# allowing exec access for debugging.
ENTRYPOINT ["/bin/bash", "-c", "source /opt/venv/bin/activate && cd /app/src && /opt/venv/bin/python -m persist.database.run_migration upgrade && /opt/venv/bin/python /app/src/main.py & tail -f /dev/null"]
