"""Load the EstatePulse app package (scorers + live snapshots) under a private module name.

The resources server's own module is called "app", so our repo's "app" package is loaded as "dreamhome_app".
DREAMHOME_ROOT points at the EstatePulse repo (default: two levels up, or ~/RedHat_NVIDIA_Hack when copied into nemo-gym).
"""
import importlib, importlib.util, os, sys
from pathlib import Path


def _root():
    env = os.getenv("DREAMHOME_ROOT")
    if env:
        return Path(env)
    here = Path(__file__).resolve().parents[2]
    return here if (here / "app" / "metrics.py").exists() else Path.home() / "RedHat_NVIDIA_Hack"


def load():
    if "dreamhome_app.metrics" in sys.modules:
        return sys.modules["dreamhome_app.metrics"], sys.modules["dreamhome_app.tools"]
    root = _root() / "app"
    spec = importlib.util.spec_from_file_location("dreamhome_app", root / "__init__.py", submodule_search_locations=[str(root)])
    pkg = importlib.util.module_from_spec(spec)
    sys.modules["dreamhome_app"] = pkg
    spec.loader.exec_module(pkg)
    return importlib.import_module("dreamhome_app.metrics"), importlib.import_module("dreamhome_app.tools")
