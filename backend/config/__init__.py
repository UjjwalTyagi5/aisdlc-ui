import os, sys, inspect

currentdir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))
backend_root = os.path.dirname(currentdir)  # platform/backend — make top-level packages importable
sys.path.insert(0, backend_root)

class sdlcSettings:

  def __init__(self):
    from config.env import AGENTIC_APP_PATH
    BACKEND_BASE_PATH = AGENTIC_APP_PATH

    self.FILES = os.path.join(BACKEND_BASE_PATH, "files")

