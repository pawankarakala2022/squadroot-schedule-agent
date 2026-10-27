"""Demo app tests: full mock-mode flow, no key, no network, no sockets.

Endpoint functions are called directly (the sandbox forbids even
loopback sockets, so no HTTP client can run here); the browser talks to
the same functions over HTTP.
"""

import pytest

pytest.importorskip("fastapi")  # demo extra: plain installs skip these tests
from fastapi import HTTPException

from app.main import (
    CLUB,
    api_approve,
    api_cancel,
    api_notices,
    api_plan,
    api_reschedule,
    api_revise,
    get_club,
    index,
    vision_result,
    voice_result,
)

PRESETS = CLUB["presets"]


def _plan() -> dict:
    body = api_plan({"mode": "mock", "request": PRESETS["plan_request"]})
    assert body["proposal"]["events"], "mock plan must place sessions"
    assert all(c["passed"] for c in body["proposal"]["check_results"])
    assert any(s["step"] == "verify" for s in body["trace"])  # coach hook runs
    return body["proposal"]


def test_club_and_index() -> None:
    response = index()
    assert str(response.path).endswith("index.html") and response.path.exists()
    club = get_club()
    assert club["teams"] and club["fields"] and club["presets"]["plan_request"]
    assert "Wednesday" in club["coach_note"]


def test_plan_revise_approve_notices_flow() -> None:
    proposal = _plan()
    revised = api_revise({"mode": "mock", "proposal": proposal, "request": "keep it"})
    assert revised["proposal"]["events"] == proposal["events"]

    result = api_approve({"proposal": proposal})["result"]
    assert result["approved"] is True

    notices = api_notices({"mode": "mock", "events": proposal["events"]})["notices"]
    assert {n["language"] for n in notices["notices"]} == {"es", "en", "vi"}  # union of team languages
    assert all(n["verified"] for n in notices["notices"])  # mock echoes the facts


def test_rain_demo_moves_only_thursday() -> None:
    proposal = _plan()
    before = {(e["starts_at"], e["field_id"]) for e in proposal["events"]}
    assert any(e["starts_at"].startswith("2026-11-12") for e in proposal["events"])
    moved = api_reschedule(
        {"mode": "mock", "events": proposal["events"], "request": PRESETS["rain_request"]}
    )["proposal"]
    assert len(moved["events"]) == len(before)
    assert "->" in moved["summary"]
    thursday = [e for e in moved["events"] if e["starts_at"].startswith("2026-11-12")]
    assert thursday and all(e["field_id"] != "f1" for e in thursday)  # off closed North
    untouched = [e for e in moved["events"] if not e["starts_at"].startswith("2026-11-12")]
    assert all((e["starts_at"], e["field_id"]) in before for e in untouched)
    assert all(c["passed"] for c in moved["check_results"])


def test_cancel_flow() -> None:
    proposal = _plan()
    cancelled = api_cancel(
        {"mode": "mock", "events": proposal["events"], "request": PRESETS["cancel_request"]}
    )["proposal"]
    assert len(cancelled["events"]) < len(proposal["events"])
    assert "Cancelled" in cancelled["summary"]


def test_mock_voice_and_vision() -> None:
    voice = voice_result("mock", b"fake-bytes", "note.wav", "audio/wav")
    assert voice["transcript"] and voice["mock"] is True

    vision = vision_result("mock", b"fake-bytes", "image/png")
    assert vision["schedule"]["games"] and vision["mock"] is True

    planned = api_plan({"mode": "mock", "request": PRESETS["plan_request"], "games": vision["schedule"]})
    assert all(c["passed"] for c in planned["proposal"]["check_results"])


def test_errors_are_readable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    with pytest.raises(HTTPException, match="MODEL_API_KEY"):
        api_plan({"mode": "meta", "request": "x"})  # no key, no network attempted
    with pytest.raises(HTTPException):
        api_notices({"mode": "mock", "events": []})
