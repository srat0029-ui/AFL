"""Minimal external-client example for the AFL Pricing & Market Intelligence
API — deliberately dependency-free (stdlib `urllib`/`json` only) so it works
as a starting point regardless of what HTTP library a real integration ends
up using.

Usage:
    AFL_API_KEY=afl_xxx python examples/b2b_client_example.py
    AFL_API_KEY=afl_xxx AFL_API_BASE_URL=https://your-deployment python examples/b2b_client_example.py

Against a `local` deployment (APP_ENV=local) AFL_API_KEY can be omitted
entirely — the server authenticates unauthenticated local requests as a
synthetic `local-dev` consumer. Any other environment requires a real key;
see backend/docs/API_USAGE.md §3 for how to obtain one, and §12 for how an
operator provisions one via the admin CLI.

This script never hardcodes a real API key — it only ever reads one from
the environment.
"""

import json
import os
import urllib.error
import urllib.request

BASE_URL = os.environ.get("AFL_API_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("AFL_API_KEY")  # may be unset in local dev — see module docstring


def _request(path: str, method: str = "GET", body: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    data = json.dumps(body).encode("utf-8") if body is not None else None

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        print(f"  -> {resp.status} {method} {path}  (request_id={resp.headers.get('X-Request-ID')})")
        return json.loads(resp.read())


def main() -> int:
    print("1. Readiness check (no auth required)")
    readiness = _request("/api/v1/pricing/readiness")
    print(f"   status={readiness['status']}  checks={len(readiness['checks'])}")
    if readiness["status"] == "not_ready":
        print("   Underlying data isn't ready yet — pricing calls below may 503. Continuing anyway to demonstrate the shape.")

    print("\n2. Current-round pricing (all matches, teams + disposals + goals)")
    round_pricing = _request("/api/v1/pricing/afl/current-round")
    print(f"   round={round_pricing['round_number']}  n_matches={round_pricing['n_matches']}")
    if not round_pricing["teams"]:
        print("   No matches priced for the current round right now — nothing further to demo.")
        return 0

    team = round_pricing["teams"][0]
    provenance = team["provenance"]
    print(f"   {team['home_team']} vs {team['away_team']}")
    print(f"   home_fair_odds={team['home_fair_odds']:.2f}  away_fair_odds={team['away_fair_odds']:.2f}")
    print(f"   model_version={provenance['model_version']}  request_id={provenance['request_id']}")

    match_id = team["match_id"]
    match_disposals = [d for d in round_pricing["disposals"] if d["match_id"] == match_id]

    print("\n3. That match's player disposal pricing")
    if match_disposals:
        first = match_disposals[0]
        print(f"   {first['player_name']}: expected={first['expected']:.1f}  is_stale={first['is_stale']}")
    else:
        print("   No disposal projections available for this match yet.")

    print("\n4. Same Game Multi pricing (that team's H2H + a disposal leg from the same match)")
    if match_disposals:
        leg_player = match_disposals[0]
        sgm_request = {
            "match_id": match_id,
            "legs": [
                {"leg_type": "h2h", "team_id": leg_player["team_id"]},
                {"leg_type": "disposals", "player_id": leg_player["player_id"], "threshold": round(leg_player["expected"]) - 0.5},
            ],
        }
        sgm = _request("/api/v1/pricing/afl/same-game", method="POST", body=sgm_request)
        print(f"   model_fair_odds={sgm['model_fair_odds']:.2f}  naive_independence_fair_odds={sgm['naive_independence_fair_odds']:.2f}")
        print(f"   correlation_adjustment_pp={sgm['correlation_adjustment_pp']:.3f}  n_simulations={sgm['n_simulations']}")
    else:
        print("   Skipped — no disposal leg available to combine.")

    print("\n5. Deliberate error: two team-market legs in one combo (only one is supported per combo in this version)")
    try:
        _request(
            "/api/v1/pricing/afl/same-game", method="POST",
            body={"match_id": match_id, "legs": [{"leg_type": "h2h"}, {"leg_type": "total", "is_over": True, "line_value": 150.5}]},
        )
    except urllib.error.HTTPError as exc:
        error_body = json.loads(exc.read())
        print(f"   -> {exc.code}  error_code={error_body.get('error_code')}  request_id={error_body.get('request_id')}")
        print("   (this is the shared error contract — see backend/docs/API_USAGE.md §6)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
