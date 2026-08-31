"""Post-deployment smoke test - verifies a running deployment (local Docker
Compose, or a real deployed URL), not just that the code imports. Stdlib
only (urllib/json), so it needs nothing installed beyond Python itself -
runnable from a CI runner or an operator's laptop with equal ease.

Never hardcodes a specific match/player: every pricing check works off
whatever the round-pricing response actually returns, so this doesn't go
stale as fixtures change (see PRICING_ENGINE.md's own "don't hardcode a
match that will become stale" convention, extended here to deployment
checks).

Usage:
    python scripts/smoke_test.py --base-url http://localhost:8000
    python scripts/smoke_test.py --base-url https://afl-backend.onrender.com --api-key afl_xxx --frontend-url https://afl-frontend.onrender.com
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"


def _get(base_url: str, path: str, api_key: str | None = None) -> tuple[int, dict | None]:
    headers = {"X-API-Key": api_key} if api_key else {}
    req = urllib.request.Request(f"{base_url}{path}", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, None
    except urllib.error.URLError:
        return 0, None


def _post(base_url: str, path: str, payload: dict, api_key: str | None = None) -> tuple[int, dict | None]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(f"{base_url}{path}", data=json.dumps(payload).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except json.JSONDecodeError:
            return exc.code, None


class SmokeTest:
    def __init__(self, base_url: str, api_key: str | None, frontend_url: str | None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.frontend_url = frontend_url.rstrip("/") if frontend_url else None
        self.results: list[tuple[str, str, str]] = []

    def _record(self, name: str, status: str, detail: str = "") -> None:
        self.results.append((name, status, detail))
        print(f"  [{status}] {name}{f' - {detail}' if detail else ''}")

    def run(self) -> int:
        print(f"Smoke testing {self.base_url}")
        self.check_liveness()
        self.check_readiness()
        self.check_release()
        self.check_frontend()
        self.check_invalid_key()
        self.check_authenticated_round_pricing()
        self.check_sgm_if_possible()
        self.check_trading_monitor()
        self.check_model_registry()

        failed = [r for r in self.results if r[1] == FAIL]
        print(f"\n{len(self.results) - len(failed)}/{len(self.results)} checks passed.")
        return 1 if failed else 0

    def check_liveness(self) -> None:
        status, body = _get(self.base_url, "/api/health")
        if status == 200 and body == {"status": "ok"}:
            self._record("liveness (/api/health)", PASS)
        else:
            self._record("liveness (/api/health)", FAIL, f"status={status} body={body}")

    def check_readiness(self) -> None:
        status, body = _get(self.base_url, "/api/health/db")
        if status == 200 and body and body.get("status") == "ok":
            self._record("database readiness (/api/health/db)", PASS, f"sport_rows={body.get('sport_rows')}")
        else:
            self._record("database readiness (/api/health/db)", FAIL, f"status={status} body={body}")

    def check_release(self) -> None:
        status, body = _get(self.base_url, "/api/release")
        if status == 200 and body and body.get("git_sha"):
            self._record("release provenance (/api/release)", PASS, f"git_sha={body['git_sha']}")
        else:
            self._record("release provenance (/api/release)", FAIL, f"status={status} body={body}")

    def check_frontend(self) -> None:
        if not self.frontend_url:
            self._record("frontend loads", SKIP, "no --frontend-url given")
            return
        status, _ = _get(self.frontend_url, "/")
        self._record("frontend loads", PASS if status == 200 else FAIL, f"status={status}")

    def check_invalid_key(self) -> None:
        status, body = _get(self.base_url, "/api/v1/pricing/afl/current-round", api_key="afl_smoke_test_invalid_key")
        if status == 401 and body and body.get("error_code") == "AUTHENTICATION_FAILED":
            self._record("invalid API key is rejected", PASS)
        else:
            self._record("invalid API key is rejected", FAIL, f"status={status} body={body}")

    def check_authenticated_round_pricing(self) -> None:
        status, body = _get(self.base_url, "/api/v1/pricing/afl/current-round", api_key=self.api_key)
        if status != 200 or body is None:
            self._record("authenticated round pricing", FAIL, f"status={status}")
            self._round_pricing = None
            return
        self._record("authenticated round pricing", PASS, f"n_matches={body.get('n_matches')}")
        self._round_pricing = body

        if body.get("teams"):
            self._record("team pricing present", PASS, f"{len(body['teams'])} team market row(s)")
        else:
            self._record("team pricing present", SKIP, "no matches priced right now")

        if body.get("disposals"):
            self._record("player pricing present", PASS, f"{len(body['disposals'])} disposal row(s)")
        else:
            self._record("player pricing present", SKIP, "no disposal projections right now")

    def check_sgm_if_possible(self) -> None:
        round_pricing = getattr(self, "_round_pricing", None)
        if not round_pricing or not round_pricing.get("teams") or not round_pricing.get("disposals"):
            self._record("SGM computation", SKIP, "no team+disposal combo available right now")
            return
        team = round_pricing["teams"][0]
        match_disposals = [d for d in round_pricing["disposals"] if d["match_id"] == team["match_id"]]
        if not match_disposals:
            self._record("SGM computation", SKIP, "no disposal leg for this match")
            return
        leg_player = match_disposals[0]
        payload = {
            "match_id": team["match_id"],
            "legs": [
                {"leg_type": "h2h", "team_id": leg_player["team_id"]},
                {"leg_type": "disposals", "player_id": leg_player["player_id"], "threshold": round(leg_player["expected"]) - 0.5},
            ],
        }
        status, body = _post(self.base_url, "/api/v1/pricing/afl/same-game", payload, api_key=self.api_key)
        if status == 200 and body and "model_fair_odds" in body:
            self._record("SGM computation", PASS, f"model_fair_odds={body['model_fair_odds']:.2f}")
        else:
            self._record("SGM computation", FAIL, f"status={status} body={body}")

    def check_trading_monitor(self) -> None:
        status, _ = _get(self.base_url, "/api/v1/trading-monitor/overview")
        self._record("Trading Monitor reachable", PASS if status == 200 else FAIL, f"status={status}")

    def check_model_registry(self) -> None:
        status, _ = _get(self.base_url, "/api/v1/model-registry")
        self._record("Model Registry reachable", PASS if status == 200 else FAIL, f"status={status}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", required=True, help="Backend base URL, e.g. http://localhost:8000")
    parser.add_argument("--api-key", default=None, help="B2B API key. Omit to test as the local-dev bypass consumer.")
    parser.add_argument("--frontend-url", default=None, help="Frontend base URL, if checking it too.")
    args = parser.parse_args()

    return SmokeTest(args.base_url, args.api_key, args.frontend_url).run()


if __name__ == "__main__":
    sys.exit(main())
