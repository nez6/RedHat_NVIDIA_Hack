"""Live check that Jev decisions are served by the local vLLM (Nemotron).

Usage: env/bin/python scripts/jev_probe.py   (needs start_nemotron.sh running)
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
os.environ.setdefault("JEV_BACKEND", "vllm")

from app import jev as J  # noqa: E402
from app import live  # noqa: E402
from app import tools as T  # noqa: E402

profile = {"summary": "Family of four working at RTP; safe area, good schools, fenced yard", "max_price": 550000,
           "annual_income": 165000, "down_payment_pct": 20, "min_beds": 3, "must_have_features": ["fenced yard"],
           "commute_destination": "rtp"}
items = []
for lid in (1001, 1002, 1003, 1004, 1005):
    L, m = T.BY_ID[lid], T.math_inputs(lid, profile)
    lv = live.safety(L["neighborhood"]) or {}
    items.append((lid, {"listing": L, "profile": profile, "math_scores": m, "live_safety_index": m["safety_index"],
                        "safety_text": lv.get("jev_state") or f"{L['neighborhood']}: baseline safety index {m['safety_index']}."}))
t0 = time.time()
out = J.decide_batch(items)
print(json.dumps({"backend": J.backend(), "wall_ms": int((time.time() - t0) * 1000),
                  "decisions": {k: {f: v.get(f) for f in ("engine", "fit", "safety", "verdict", "confidence", "ms", "error")}
                                for k, v in out.items()}}, indent=1, default=str))
sys.exit(0 if all(v["engine"] == "jev" for v in out.values()) else 1)
