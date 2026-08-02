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
        # zstd for pg_dump backup compression (used by pg_backup_executor)
        zstd \
        # gosu for privilege drop in entrypoint (root → botuser)
        gosu \
        # vim and sudo for debugging — comment out once stable
        # vim \
        # sudo \
    # CVE-2026-27135: libnghttp2-14 DoS via malformed HTTP/2 frames after session
    # termination. Fixed in 1.64.0-1.1+deb13u1 (trixie security).
    # CVE-2026-7598 / CVE-2026-55200 (+ SSH_MSG_EXT_INFO and SFTP-response DoS):
    # libssh2-1t64 integer overflow in userauth_password and out-of-bounds write in
    # the transport layer. Fixed in 1.11.1-1+deb13u1 (trixie security). libssh2 is
    # resident only as a transitive dep of git/libcurl. Upgrade both explicitly so
    # the fixes are pulled regardless of base-image snapshot date.
    # CVE-2026-34743: liblzma5 DoS via buffer overflow in index decoding.
    # Fixed in 5.8.1-1+deb13u1 (trixie security). liblzma5 is a transitive dep
    # (git, dpkg, python's lzma module); upgrade explicitly so the fix is
    # pulled regardless of base-image snapshot date.
    # CVE-2025-59375, CVE-2026-24515/25210/32776/32777/32778/41080/45186/50219/
    # 56131/56132/56403–56412: 21 libexpat1 CVEs (4 HIGH — integer overflows in
    # storeAtts/addBinding/copyString/XML_ParseBuffer reachable as arbitrary code
    # execution, plus use-after-free and assorted parser DoS). Fixed in
    # 2.8.2-1~deb13u1 (trixie security); the base image ships 2.7.1-2. libexpat1
    # is a transitive dep (python3's pyexpat, dpkg, git) — upgrade explicitly so
    # the fix is pulled regardless of base-image snapshot date.
    && apt-get install -y --only-upgrade libnghttp2-14 libssh2-1t64 liblzma5 libexpat1 \
    && apt-get autoremove -y \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create a virtual environment
RUN python -m venv /opt/venv

# ── System-Python pip hardening (CVE-2026-3219 / CVE-2026-6357) ─────────────
# python:3.13-slim-trixie ships pip 26.0.1 in the system Python at
# /usr/local/lib/python3.13/site-packages/. We use /opt/venv/bin/pip for all
# actual installs, so the system pip is dead weight. Purge the dist-info so
# Trivy stops reporting it. The venv pip is upgraded to >=26.1 in the next
# RUN step and is unaffected (it lives in a separate tree: /opt/venv/).
#
# IMPORTANT: Use the absolute path /usr/local/bin/python3 — the PATH already
# prepends /opt/venv/bin/, so bare `python` resolves to the venv interpreter.
# Asking the venv for its purelib would target /opt/venv, nuking the venv pip.
RUN SYS_PY=/usr/local/bin/python3 && \
    SYS_SITE_PURELIB=$("${SYS_PY}" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])') && \
    DEB_SITE=/usr/lib/python3/dist-packages && \
    echo "System interpreter:    ${SYS_PY}" && \
    echo "System site-packages:  ${SYS_SITE_PURELIB}" && \
    # Sanity guard — refuse to nuke the venv if path detection points there \
    case "${SYS_SITE_PURELIB}" in /opt/venv/*) echo "ERROR: refusing to scrub venv site-packages"; exit 1 ;; esac && \
    rm -rf "${SYS_SITE_PURELIB}"/pip "${SYS_SITE_PURELIB}"/pip-*.dist-info && \
    rm -rf "${DEB_SITE}"/pip "${DEB_SITE}"/pip-*.dist-info && \
    if ls "${SYS_SITE_PURELIB}"/pip-*.dist-info >/dev/null 2>&1 || \
       ls "${DEB_SITE}"/pip-*.dist-info >/dev/null 2>&1; then \
        echo "ERROR: system pip dist-info still present"; exit 1; \
    fi && \
    /opt/venv/bin/python -c 'import pip; print("venv pip OK:", pip.__version__)'

WORKDIR /app

# Upgrade pip toolchain
# CVE-2026-6357: pip < 26.1 arbitrary code execution via malicious wheel.
# CVE-2026-3219: pip < 26.1 incorrect file installation via improper archive handling.
# Pin to >=26.1 explicitly so the fix is enforced regardless of base-image snapshot.
RUN /opt/venv/bin/pip install --upgrade --no-cache-dir \
    "pip>=26.1" \
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

# ── venv pip purge (alerts #1124–#1126) ─────────────────────────────────────
# pip 26.2 ships pip/_vendor/bom.cdx.json — a CycloneDX SBOM of its vendored
# dependencies — which Trivy reads and reports against: msgpack 1.1.2
# (GHSA-6v7p-g79w-8964, HIGH) and setuptools 70.3.0 (CVE-2025-47273 HIGH,
# CVE-2026-59890). Neither is fixable by upgrading pip — the vendored copies
# only move when pip re-vendors them. Every install is complete by this point
# and nothing at runtime invokes pip, so drop it; same reasoning as the
# system-pip purge above.
RUN rm -rf /opt/venv/lib/python*/site-packages/pip \
           /opt/venv/lib/python*/site-packages/pip-*.dist-info \
           /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.* && \
    if ls /opt/venv/lib/python*/site-packages/pip-*.dist-info >/dev/null 2>&1; then \
        echo "ERROR: venv pip dist-info still present"; exit 1; \
    fi && \
    /opt/venv/bin/python -c 'import sys; print("venv python OK:", sys.version.split()[0])'

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

HEALTHCHECK --interval=30s --timeout=5s --start-period=45s --retries=3 \
  CMD curl -s -o /dev/null -f http://localhost:8000/api/v1/health/ || exit 1

# Sanity-check gosu is functional before the image is sealed.
RUN gosu nobody true

# Copy the entrypoint script (chmod set on host; Dockerfile RUN ensures execute bit).
COPY services/bot-core/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# The entrypoint starts as root, normalizes /app/data ownership, then drops to
# botuser via gosu.  The '& tail -f /dev/null' keep-alive (inside the gosu'd
# command) preserves original crash-survival behavior.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
