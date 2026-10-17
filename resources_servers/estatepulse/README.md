# EstatePulse NeMo Gym environment

One resources server exposing the 10 EstatePulse metric tools (same code as production: `app/metrics.py`).

| Agent | NeMo Gym mode | Reward |
|---|---|---|
| Lifestyle, School, Safety, Environment, Urban, Commute, Rooms, Maintenance | eval | 0.5 for calling its tool with every listing id + 0.5 for naming the true best listing |
| Value / Forecast | eval + RL rollouts | 1 − abs error (pp) / 2 on 12-month appreciation; −0.2 without a tool call |
| Deal Calculator | eval + RL rollouts | 1.0 best deal, 0.5 runner-up; −0.2 without a tool call |

Targets come from the deterministic scorers today. As realized closing prices arrive, replace `target_pct` with the
realized appreciation and rebuild the dataset — that is the self-improving loop.

Run with `scripts/gym_eval.sh` from the repo root (copies this folder into ~/nemo-gym, points it at the local vLLM
Nemotron server, runs the 8 eval harnesses, then collects RL rollouts with 4 repeats per task). Licensed Apache-2.0 like NeMo Gym.
