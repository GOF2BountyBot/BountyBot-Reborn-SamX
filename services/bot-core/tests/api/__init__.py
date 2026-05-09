# API tests package
#
# IMPORTANT: This __init__.py causes pytest to register tests/api as the
# top-level "api" package, which shadows src/api (the real application code).
# The fix is applied in tests/api/conftest.py which purges the stale entries
# and ensures src/ is first on sys.path before any test imports run.
