"""Reward functions for the EstatePulse NeMo Gym environment (pure Python, unit-tested in our CI).

Eval tasks (8 metric agents): did the agent call ITS tool with ALL listing ids, and name the right best listing?
RL tasks:
  - deal:     pick the best deal among candidates -> 1.0 exact, 0.5 runner-up, else 0 (minus 0.2 if no tool call)
  - forecast: predict 12-month appreciation %     -> 1 - |err| / 2pp, floored at 0 (minus 0.2 if no tool call)
Targets come from app/metrics.py today; swap in realized closing-price appreciation as it accrues (self-improving loop).
"""
import json
import re

ANSWER_RE = re.compile(r"ANSWER:\s*(-?[\d,]+(?:\.\d+)?)", re.I)


def tool_calls(output_items):
    """[(name, args_dict)] from a Responses API output list (objects or dicts)."""
    calls = []
    for it in output_items or []:
        get = (lambda k: it.get(k)) if isinstance(it, dict) else (lambda k: getattr(it, k, None))
        if get("type") == "function_call":
            try:
                args = json.loads(get("arguments") or "{}")
            except (TypeError, ValueError):
                args = {}
            calls.append((get("name"), args))
    return calls


def parse_answer(text):
    m = ANSWER_RE.findall(text or "")
    return float(m[-1].replace(",", "")) if m else None


def eval_reward(task, calls, text):
    want_tool = f"get_{task['metric']}_inputs"
    used = [a for n, a in calls if n == want_tool]
    covered = bool(used) and set(task["listing_ids"]) <= {int(x) for a in used for x in (a.get("listing_ids") or [])}
    best_ok = parse_answer(text) == task["best_id"]
    return 0.5 * covered + 0.5 * best_ok, {"tool_covered": covered, "best_correct": best_ok}


def deal_reward(task, calls, text):
    ans = parse_answer(text)
    ranking = task["ranking"]  # listing ids, best first
    r = 1.0 if ans == ranking[0] else 0.5 if len(ranking) > 1 and ans == ranking[1] else 0.0
    used = any(n == "get_deal_inputs" for n, _ in calls)
    return max(0.0, r - (0 if used else 0.2)), {"answer": ans, "best": ranking[0], "tool_used": used}


def forecast_reward(task, calls, text):
    ans = parse_answer(text)
    used = any(n == "get_forecast_inputs" for n, _ in calls)
    if ans is None:
        return 0.0, {"answer": None, "target": task["target_pct"], "tool_used": used}
    err = abs(ans - task["target_pct"])
    return max(0.0, 1 - err / 2.0 - (0 if used else 0.2)), {"answer": ans, "target": task["target_pct"], "abs_err_pp": round(err, 3), "tool_used": used}


def reward(task, output_items, text):
    calls = tool_calls(output_items)
    fn = {"eval": eval_reward, "deal": deal_reward, "forecast": forecast_reward}[task["task_type"]]
    return fn(task, calls, text)
