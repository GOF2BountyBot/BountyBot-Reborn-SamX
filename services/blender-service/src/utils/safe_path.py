"""
Path-traversal safety utilities for blender-service.

All user-controlled filesystem paths MUST be validated through these helpers
before being used in any file-system operation.  The helpers enforce that the
resolved path stays inside a declared *base* directory, preventing ``../``
traversal and absolute-path escape attacks.

Two variants are provided:

``safe_join_http(base, user_input)``
    For use inside FastAPI route handlers.  Raises :exc:`fastapi.HTTPException`
    with status 400 on rejection so the error propagates as an HTTP response.

``safe_join(base, user_input)``
    For use inside service-layer code that must remain framework-agnostic.
    Raises :exc:`ValueError` on rejection so callers can decide how to surface
    the error.

Implementation note — CodeQL sanitizer compatibility
-----------------------------------------------------
CodeQL's ``py/path-injection`` query recognises only specific patterns as
taint barriers:

* **PathNormalization**: ``os.path.realpath()``, ``os.path.abspath()``, or
  ``os.path.normpath()``
* **SafeAccessCheck**: ``resolved_path.startswith(trusted_prefix)`` used as a
  *guard* (an ``if`` condition whose true/false branch raises or exits).

We therefore use ``os.path.realpath()`` for normalisation and
``str.startswith()`` (applied to both strings) for the safety check so that
CodeQL can statically verify the taint is broken.

Environment variable
--------------------
``BLENDER_DATA_ROOT``  (default: ``/app/data``)
    The filesystem root that all user-supplied paths must reside under.
    Override in tests by setting this variable before importing the module
    (e.g. ``os.environ["BLENDER_DATA_ROOT"] = "/tmp"`` in ``conftest.py``).
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Configurable data root — all user-controlled paths must resolve under here.
# ---------------------------------------------------------------------------

#: Filesystem root for all user-supplied paths.  Configurable via env var so
#: tests can point it at /tmp without touching production defaults.
BLENDER_DATA_ROOT: Path = Path(os.environ.get("BLENDER_DATA_ROOT", "/app/data"))


def _resolved_base_str() -> str:
    """Return the current resolved data-root as a string with trailing separator.

    Re-reading each call allows the env var to be changed by test fixtures
    *after* module import (e.g. via ``monkeypatch.setenv``).

    The trailing ``os.sep`` ensures that prefix matches are directory-level:
    e.g. ``/tmp/data`` will NOT match against a base of ``/tmp/dat``.
    """
    base = os.path.realpath(os.environ.get("BLENDER_DATA_ROOT", "/app/data"))
    if not base.endswith(os.sep):
        base = base + os.sep
    return base


def safe_join_http(base: Path | str, user_input: str) -> Path:
    """Resolve *user_input* under *base*, rejecting path-traversal attempts.

    Uses ``os.path.realpath`` (recognised by CodeQL as a normalisation step)
    and ``str.startswith`` (recognised by CodeQL as a safe-access check) to
    break the taint chain.

    :param base: The allowed root directory.  Need not exist at call time.
    :param user_input: The untrusted path string supplied by the caller.
    :return: The resolved, validated :class:`Path`.
    :raises fastapi.HTTPException: HTTP 400 if *user_input* is empty, contains
        null bytes, or resolves outside *base*.
    """
    if not user_input:
        raise HTTPException(status_code=400, detail="path must not be empty")
    if "\x00" in user_input:
        raise HTTPException(status_code=400, detail="path contains null bytes")

    base_real = os.path.realpath(str(base))
    if not base_real.endswith(os.sep):
        base_real = base_real + os.sep

    try:
        # Joining base with user_input and resolving handles both relative
        # ('../escape') and absolute ('/etc/passwd') escape attempts.
        candidate_real = os.path.realpath(os.path.join(base_real, user_input))
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid path: {exc}") from exc

    # SafeAccessCheck: startswith is recognised by CodeQL as a taint barrier.
    if not candidate_real.startswith(base_real):
        raise HTTPException(status_code=400, detail="path escapes allowed directory")
    return Path(candidate_real)


def safe_join(base: Path | str, user_input: str) -> Path:
    """Resolve *user_input* under *base*, rejecting path-traversal attempts.

    Framework-agnostic variant: raises :exc:`ValueError` instead of an HTTP
    exception, suitable for service-layer code.

    :param base: The allowed root directory.  Need not exist at call time.
    :param user_input: The untrusted path string supplied by the caller.
    :return: The resolved, validated :class:`Path`.
    :raises ValueError: If *user_input* is empty, contains null bytes, or
        resolves outside *base*.
    """
    if not user_input:
        raise ValueError("path must not be empty")
    if "\x00" in user_input:
        raise ValueError("path contains null bytes")

    base_real = os.path.realpath(str(base))
    if not base_real.endswith(os.sep):
        base_real = base_real + os.sep

    try:
        candidate_real = os.path.realpath(os.path.join(base_real, user_input))
    except (ValueError, OSError) as exc:
        raise ValueError(f"invalid path: {exc}") from exc

    if not candidate_real.startswith(base_real):
        raise ValueError("path escapes allowed directory")
    return Path(candidate_real)


def validate_user_path_http(user_input: str, *, description: str = "path") -> Path:
    """Validate that *user_input* resolves within ``BLENDER_DATA_ROOT``.

    Accepts a **full absolute path** from the caller (e.g.
    ``/app/data/game-objects/ships/foo.obj``) and checks that its resolved
    form is a descendant of ``BLENDER_DATA_ROOT``.

    Uses ``os.path.realpath`` for normalisation and ``str.startswith`` for the
    safe-access check so that CodeQL recognises the taint as broken.

    :param user_input: The untrusted path string from the caller.
    :param description: Human-readable label used in error messages.
    :return: The resolved :class:`Path`.
    :raises fastapi.HTTPException: HTTP 400 if *user_input* is empty, contains
        null bytes, or resolves outside ``BLENDER_DATA_ROOT``.
    """
    if not user_input:
        raise HTTPException(status_code=400, detail=f"{description} must not be empty")
    if "\x00" in user_input:
        raise HTTPException(status_code=400, detail=f"{description} contains null bytes")

    data_root = _resolved_base_str()
    try:
        resolved = os.path.realpath(user_input)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid {description}: {exc}") from exc

    # SafeAccessCheck: startswith is the pattern recognised by CodeQL.
    if not resolved.startswith(data_root):
        raise HTTPException(
            status_code=400,
            detail=f"{description} must be within the allowed data directory",
        )
    return Path(resolved)


def validate_user_path(user_input: str, *, description: str = "path") -> Path:
    """Validate that *user_input* resolves within ``BLENDER_DATA_ROOT``.

    Framework-agnostic variant: raises :exc:`ValueError` instead of an HTTP
    exception, suitable for service-layer code.

    :param user_input: The untrusted path string from the caller.
    :param description: Human-readable label used in error messages.
    :return: The resolved :class:`Path`.
    :raises ValueError: If *user_input* is empty, contains null bytes, or
        resolves outside ``BLENDER_DATA_ROOT``.
    """
    if not user_input:
        raise ValueError(f"{description} must not be empty")
    if "\x00" in user_input:
        raise ValueError(f"{description} contains null bytes")

    data_root = _resolved_base_str()
    try:
        resolved = os.path.realpath(user_input)
    except (ValueError, OSError) as exc:
        raise ValueError(f"invalid {description}: {exc}") from exc

    if not resolved.startswith(data_root):
        raise ValueError(f"{description} must be within the allowed data directory")
    return Path(resolved)
