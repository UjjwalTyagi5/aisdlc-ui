import sys
import os

# Ensure agentic_app/ is on sys.path so `from shared.models import ...` resolves
# when pytest is run from the repo root (where pyproject.toml lives) rather than
# from agentic_app/ directly.
_agentic_app_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
_agentic_app_dir = os.path.normpath(_agentic_app_dir)
if _agentic_app_dir not in sys.path:
    sys.path.insert(0, _agentic_app_dir)
