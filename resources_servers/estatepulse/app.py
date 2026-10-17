"""EstatePulse NeMo Gym resources server.

Exposes the same 10 metric tools the production agents use (backed by app/metrics.py + live snapshots) and verifies:
  * 8 eval harnesses  — lifestyle, school, safety, environment, urban, commute, rooms, maintenance
  * eval + RL rollouts — forecast (Value/Forecast agent) and deal (Deal Calculator agent)
"""
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI
from pydantic import BaseModel

from nemo_gym.base_resources_server import (
    BaseResourcesServerConfig,
    BaseRunRequest,
    BaseVerifyRequest,
    BaseVerifyResponse,
    SimpleResourcesServer,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rewards  # noqa: E402
from core import load  # noqa: E402

M, _T = load()
# RL tasks must reason from raw inputs, so the final answer fields are withheld from those tools
HIDDEN = {"forecast": ("score", "expected_12m_pct", "forecast_12m_value"), "deal": ("score",)}


class EstatePulseConfig(BaseResourcesServerConfig):
    pass


class MetricToolRequest(BaseModel):
    listing_ids: list[int]
    buyer: Optional[dict[str, Any]] = None


class MetricToolResponse(BaseModel):
    results: list[dict[str, Any]]


class EstatePulseRunRequest(BaseRunRequest):
    task: dict[str, Any]


class EstatePulseVerifyRequest(EstatePulseRunRequest, BaseVerifyRequest):
    pass


class EstatePulseVerifyResponse(BaseVerifyResponse):
    task: dict[str, Any]
    details: dict[str, Any] = {}


class EstatePulseResourcesServer(SimpleResourcesServer):
    checkpoint_mode = "stateless"  # tool results depend only on the request
    config: EstatePulseConfig

    def setup_webserver(self) -> FastAPI:
        app = super().setup_webserver()
        for metric in M.METRICS:
            app.post(f"/{M.tool_name(metric)}")(self._tool(metric))
        return app

    @staticmethod
    def _tool(metric):
        hidden = HIDDEN.get(metric, ())

        async def handler(body: MetricToolRequest) -> MetricToolResponse:
            rows = M.run_tool(metric, body.listing_ids, body.buyer or {})
            return MetricToolResponse(results=[{k: v for k, v in r.items() if k not in hidden} for r in rows])
        handler.__name__ = M.tool_name(metric)
        return handler

    async def verify(self, body: EstatePulseVerifyRequest) -> EstatePulseVerifyResponse:
        score, details = rewards.reward(body.task, body.response.output, body.response.output_text)
        return EstatePulseVerifyResponse(**body.model_dump(), reward=float(score), details=details)


if __name__ == "__main__":
    EstatePulseResourcesServer.run_webserver()
