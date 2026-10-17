"""Data contracts. Every snapshot must pass these before it replaces the last good one."""
import datetime as dt


class ContractError(ValueError):
    pass


def check_fred(fred, today=None, max_age_days=45):
    today = today or dt.date.today()
    problems = []
    m = fred.get("mortgage_30y")
    if not m:
        raise ContractError("mortgage_30y missing (required for affordability math)")
    if not (1.0 <= m["value"] <= 20.0):
        raise ContractError(f"mortgage_30y out of range: {m['value']}")
    age = (today - dt.date.fromisoformat(m["date"])).days
    if age > max_age_days:
        problems.append(f"mortgage_30y is {age} days old")
    for k, s in fred.items():
        if "value" not in s or "date" not in s:
            raise ContractError(f"{k} malformed")
    return problems


def check_safety(safety, expected_hoods):
    problems = []
    for name, p in safety.items():
        if not (0 <= p["safety_index"] <= 100):
            raise ContractError(f"{name} safety_index out of range: {p['safety_index']}")
        if p["incidents"] < 0:
            raise ContractError(f"{name} negative incident count")
    missing = [h for h in expected_hoods if h not in safety]
    if missing:
        problems.append(f"no live safety for {len(missing)} neighborhoods (baseline used): {', '.join(missing[:4])}…")
    raleigh = [p for p in safety.values() if p["source"] == "raleigh_pd"]
    if raleigh and sum(p["incidents"] for p in raleigh) == 0:
        raise ContractError("Raleigh feed returned zero incidents near every neighborhood — feed likely broken")
    return problems
