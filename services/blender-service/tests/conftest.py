"""
Shared fixtures and import path setup for blender-service tests.

Ensures that:
1. src/ is at position 0 in sys.path (so service modules are importable)
2. services/ parent is on sys.path (so shared bblogger is importable)
3. BLENDER_DATA_ROOT is set to /tmp so path-validation helpers allow tmp_path
"""

import os
import sys

# ---------------------------------------------------------------------------
# 0. Set BLENDER_DATA_ROOT before any service imports so safe_path uses /tmp.
#    In production the env var defaults to /app/data; in tests we allow /tmp
#    so pytest tmp_path fixtures pass validation without special-casing.
# ---------------------------------------------------------------------------
os.environ.setdefault("BLENDER_DATA_ROOT", "/tmp")

# ---------------------------------------------------------------------------
# 1. Ensure src/ is first on sys.path
# ---------------------------------------------------------------------------
_SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
elif sys.path[0] != _SRC_DIR:
    sys.path.remove(_SRC_DIR)
    sys.path.insert(0, _SRC_DIR)

# ---------------------------------------------------------------------------
# 2. Ensure services/ parent is on sys.path (for `from shared import bblogger`)
# ---------------------------------------------------------------------------
# Walk up to find the directory containing the 'shared' package
# The shared module lives at /proj/services/shared/
_SHARED_PARENT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _SHARED_PARENT not in sys.path:
    sys.path.insert(1, _SHARED_PARENT)
