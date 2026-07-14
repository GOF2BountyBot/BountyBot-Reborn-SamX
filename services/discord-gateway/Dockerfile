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
    && apt-get install -y --only-upgrade libnghttp2-14 libssh2-1t64 liblzma5 \
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

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fs http://localhost:7999/api/v1/health || exit 1

# Sanity-check gosu is functional before the image is sealed.
RUN gosu nobody true

# Copy the entrypoint script (chmod set on host; Dockerfile RUN ensures execute bit).
COPY services/discord-gateway/docker-entrypoint.sh /app/docker-entrypoint.sh
RUN chmod +x /app/docker-entrypoint.sh

# The entrypoint starts as root, normalizes /app/data ownership, then drops to
# botuser via gosu.  The '& tail -f /dev/null' keep-alive (inside the gosu'd
# command) preserves original crash-survival behavior.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
