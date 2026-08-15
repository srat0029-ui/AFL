"""Tests for the Wayback Machine fallback transport. subprocess.run is
mocked throughout — no real network calls — mirroring how curl_transport()
itself (app/providers/afl/afltables.py) is exercised only via injected fake
transports in provider tests, not tested at the subprocess level directly;
wayback_transport's extra logic (two-step resolve, header/body splitting on
a single vs stacked-redirect response) is real and non-trivial enough to
warrant direct coverage here, unlike the simpler curl_transport.
"""

from unittest.mock import patch

from app.providers.afl.wayback_transport import _target_wayback_year, wayback_transport


def test_target_wayback_year_uses_year_after_season_in_gbg_url():
    assert _target_wayback_year("https://afltables.com/afl/stats/teams/adelaide/2024_gbg.html") == 2025


def test_target_wayback_year_uses_year_after_season_in_team_stats_url():
    assert _target_wayback_year("https://afltables.com/afl/stats/2016t.html") == 2017


def test_target_wayback_year_falls_back_to_current_year_when_no_year_present():
    import datetime

    assert _target_wayback_year("https://afltables.com/afl/index.html") == datetime.date.today().year


def _mock_run(resolve_stdout, resolve_returncode, fetch_stdout, fetch_returncode):
    def run(cmd, **kwargs):
        result = type("Result", (), {})()
        if "-w" in cmd:  # the resolve step
            result.stdout = resolve_stdout
            result.returncode = resolve_returncode
        else:  # the fetch step
            result.stdout = fetch_stdout
            result.returncode = fetch_returncode
        return result

    return run


def test_wayback_transport_returns_status_and_body_on_success():
    resolve_stdout = "https://web.archive.org/web/20250108125025/https://afltables.com/afl/stats/teams/adelaide/2024_gbg.html"
    fetch_stdout = "HTTP/1.1 200 OK\nContent-Type: text/html\n\n<html>real page content</html>"
    with patch("subprocess.run", side_effect=_mock_run(resolve_stdout, 0, fetch_stdout, 0)):
        status, ctype, body = wayback_transport("https://afltables.com/afl/stats/teams/adelaide/2024_gbg.html")

    assert status == 200
    assert body == "<html>real page content</html>"


def test_wayback_transport_returns_zero_status_when_resolve_step_fails():
    with patch("subprocess.run", side_effect=_mock_run("", 1, "", 0)):
        status, ctype, body = wayback_transport("https://afltables.com/afl/stats/teams/adelaide/2024_gbg.html")

    assert status == 0
    assert body == ""


def test_wayback_transport_returns_zero_status_when_resolve_output_is_not_a_url():
    with patch("subprocess.run", side_effect=_mock_run("not a url", 0, "", 0)):
        status, ctype, body = wayback_transport("https://afltables.com/afl/stats/teams/adelaide/2024_gbg.html")

    assert status == 0


def test_wayback_transport_returns_zero_status_when_fetch_step_fails():
    resolve_stdout = "https://web.archive.org/web/20250108125025/https://afltables.com/afl/stats/teams/adelaide/2024_gbg.html"
    with patch("subprocess.run", side_effect=_mock_run(resolve_stdout, 0, "", 1)):
        status, ctype, body = wayback_transport("https://afltables.com/afl/stats/teams/adelaide/2024_gbg.html")

    assert status == 0
    assert body == ""


def test_wayback_transport_handles_a_body_containing_blank_lines():
    """Regression test for a real bug: the body itself can contain blank
    lines (ordinary HTML), so the header/body split must use the FIRST
    blank line only (there's exactly one hop after the resolve step, so
    this is unambiguous) - not risk matching a blank line inside the body."""
    resolve_stdout = "https://web.archive.org/web/20250108125025/https://afltables.com/afl/stats/teams/adelaide/2024_gbg.html"
    fetch_stdout = "HTTP/1.1 200 OK\nContent-Type: text/html\n\n<html>\n\nline with a blank line above it\n\n</html>"
    with patch("subprocess.run", side_effect=_mock_run(resolve_stdout, 0, fetch_stdout, 0)):
        status, ctype, body = wayback_transport("https://afltables.com/afl/stats/teams/adelaide/2024_gbg.html")

    assert status == 200
    assert "line with a blank line above it" in body
