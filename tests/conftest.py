"""Hermetic test setup: fixture snapshots, no network, Jev in rules mode, Nemotron unreachable."""
import json, os, sys

HERE = os.path.dirname(__file__)
FIX = os.path.join(HERE, "fixtures")
os.environ["DREAMHOME_LIVE_DIR"] = os.path.join(FIX, "live")
os.environ.pop("TYPESAFE_API_KEY", None)
os.environ["JEV_BACKEND"] = "off"  # hermetic: tests opt into the vllm / typesafe backends explicitly
os.environ.setdefault("NEMOTRON_BASE_URL", "http://127.0.0.1:9/v1")  # closed port -> instant connection error
os.environ.setdefault("NEMOTRON_TIMEOUT", "2")
os.environ.setdefault("NEMOTRON_MAX_RETRIES", "0")
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))


def fixture(name):
    path = os.path.join(FIX, name)
    with open(path) as f:
        return f.read() if name.endswith(".csv") else json.load(f)
