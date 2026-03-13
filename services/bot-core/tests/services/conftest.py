"""Service-level test configuration."""
import os
import sys

# Add src and src/services to path so all imports work
src_path = os.path.join(os.path.dirname(__file__), "..", "..", "src")
sys.path.insert(0, src_path)
sys.path.insert(0, os.path.join(src_path, "services"))
