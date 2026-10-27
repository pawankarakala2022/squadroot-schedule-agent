"""Offline tests for schedule_agent/notify.py. Fake chat, no network."""

import json
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from schedule_agent.errors import AgentError, ModelError
from schedule_agent.models import Field, Problem, ProposedEvent, Team
from schedule_agent.notify import FamilyNotice, FamilyNotices, draft_notices

TZ = "America/Toronto"
TZINFO = ZoneInfo(TZ)


def _problem() -> Problem:
    return Problem(
        teams=[Team(id="t1", name="Foxes"), Team(id="t2", name="Badgers")],
        fields=[
            Field(id="f1", name="North Field", open_from=time(17, 0), open_until=time(20, 0)),
            Field(id="f2", name="South Field", open_from=time(17, 0), open_until=time(20, 0)),
        ],
        season_start=date(2026, 11, 2),
        season_end=date(2026, 11, 15),
        timezone=TZ,
    )


def _events() -> list[ProposedEvent]:
    return [
        ProposedEvent(
            team_id="t1",
            field_id="f1",
            event_type="practice",
            title="Tuesday session",
            starts_at=datetime(2026, 11, 3, 17, 0, tzinfo=TZINFO),
            ends_at=datetime(2026, 11, 3, 18, 0, tzinfo=TZINFO),
        ),
        ProposedEvent(
            team_id="t2",
            field_id="f2",
            event_type="practice",
            title="",
            starts_at=datetime(2026, 11, 4, 17, 30, tzinfo=TZINFO),
            ends_at=datetime(2026, 11, 4, 18, 30, tzinfo=TZINFO),
        ),
    ]


TOKENS = ["Foxes", "Badgers", "North Field", "South Field", "2026-11-03", "17:00", "18:00",
          "2026-11-04", "17:30", "18:30"]


def _chat_script(responses: list[str]):  # type: ignore[no-untyped-def]
    """Fake chat: pop one scripted response per call, recording calls."""
    calls: list[list[dict]] = []

    def chat(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        calls.append(messages)
        assert response_format is not None
        assert response_format["json_schema"]["name"] == "family_notices"
        assert response_format["json_schema"]["strict"] is True
        return responses.pop(0)

    chat.calls = calls  # type: ignore[attr-defined]
    return chat


def _notices_payload(texts: dict[str, str], notes: object = None) -> str:
    return json.dumps(
        {
            "notices": [{"language": lang, "text": text} for lang, text in texts.items()],
            "notes": [] if notes is None else notes,
        }
    )


def test_drafts_each_language_verified() -> None:
    es = "Hola familias Foxes: práctica 2026-11-03 17:00-18:00 en North Field. Badgers: 2026-11-04 17:30-18:30 en South Field."
    en = "Hi Foxes families: practice 2026-11-03 17:00-18:00 at North Field. Badgers: 2026-11-04 17:30-18:30 at South Field."
    chat = _chat_script([_notices_payload({"es": es, "en": en})])
    result = draft_notices(_problem(), _events(), chat, languages=["es", "en"])
    assert result.all_verified
    assert [n.language for n in result.notices] == ["es", "en"]
    for notice in result.notices:
        for token in TOKENS:
            assert token in notice.text
        assert notice.missing == []
    assert len(chat.calls) == 1  # no retry when everything verifies


def test_repair_retry_fixes_missing_fact() -> None:
    bad = "Hola Foxes: práctica 2026-11-03 17:00-18:00."  # no fields, no Badgers
    good = "Hola familias Foxes: 2026-11-03 17:00-18:00 en North Field. Badgers: 2026-11-04 17:30-18:30 en South Field."
    chat = _chat_script([_notices_payload({"es": bad}), _notices_payload({"es": good})])
    result = draft_notices(_problem(), _events(), chat, languages=["es"])
    assert result.all_verified
    assert len(chat.calls) == 2  # exactly one repair retry
    assert "North Field" in chat.calls[1][-1]["content"]  # retry names the missing facts


def test_persistent_gap_reported_never_silent() -> None:
    bad = "Hi families: practice moved, see you there!"  # no exact facts at all
    chat = _chat_script([_notices_payload({"en": bad}), _notices_payload({"en": bad})])
    result = draft_notices(_problem(), _events(), chat, languages=["en"])
    (notice,) = result.notices
    assert notice.verified is False
    assert set(notice.missing) == set(TOKENS)
    assert any("en" in note for note in result.notes)
    assert len(chat.calls) == 2  # one retry, then report instead of raising


def test_unknown_and_duplicate_languages_dropped() -> None:
    text = "Hi Foxes families: 2026-11-03 17:00-18:00 at North Field. Badgers: 2026-11-04 17:30-18:30 at South Field."
    chat = _chat_script(
        [_notices_payload({"en": text, "fr": "Bonjour.", "en ": "dup"})]
    )
    result = draft_notices(_problem(), _events(), chat, languages=["en", "EN", "", "en"])
    assert [n.language for n in result.notices] == ["en"]
    assert any("Duplicate" in note for note in result.notes)
    assert any("was empty" in note for note in result.notes)
    assert any("Unknown language" in note for note in result.notes)
    assert result.all_verified  # the kept draft still verifies


def test_empty_languages_and_empty_events_fail_safely() -> None:
    chat = _chat_script(["{}"])
    with pytest.raises(AgentError):
        draft_notices(_problem(), _events(), chat, languages=[])
    assert len(chat.calls) == 0  # validated before any model call
    with pytest.raises(AgentError):
        draft_notices(_problem(), [], chat, languages=["en"])
    assert len(chat.calls) == 0


def test_unusable_model_output_raises_agent_error() -> None:
    chat = _chat_script(["not json", "{still not json"])
    with pytest.raises(AgentError):
        draft_notices(_problem(), _events(), chat, languages=["en"])
    assert len(chat.calls) == 2


def test_provider_failure_becomes_model_error() -> None:
    def down(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        raise ModelError("boom")

    with pytest.raises(ModelError):
        draft_notices(_problem(), _events(), down, languages=["en"])

    def weird(messages, *, response_format=None):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    with pytest.raises(ModelError):
        draft_notices(_problem(), _events(), weird, languages=["en"])


def test_non_latin_draft_verifies_with_exact_tokens() -> None:
    hi = "प्रिय Foxes परिवारों: अभ्यास 2026-11-03 17:00-18:00 North Field पर। Badgers: 2026-11-04 17:30-18:30 South Field पर।"
    chat = _chat_script([_notices_payload({"hi": hi})])
    result = draft_notices(_problem(), _events(), chat, languages=["hi"])
    assert result.all_verified


def test_transliterated_names_do_not_verify() -> None:
    # Names rewritten in another script must fail verification, not pass silently.
    hi = "प्रिय परिवारों: अभ्यास 2026-11-03 17:00-18:00 उत्तर मैदान पर।"
    fixed = "प्रिय Foxes परिवारों: 2026-11-03 17:00-18:00 North Field पर। Badgers: 2026-11-04 17:30-18:30 South Field पर।"
    chat = _chat_script([_notices_payload({"hi": hi}), _notices_payload({"hi": fixed})])
    result = draft_notices(_problem(), _events(), chat, languages=["hi"])
    assert result.all_verified
    assert len(chat.calls) == 2


def test_privacy_only_names_dates_times_reach_model() -> None:
    chat = _chat_script([_notices_payload({"en": "x Foxes y 2026-11-03 17:00-18:00 North Field z Badgers 2026-11-04 17:30-18:30 South Field w"})])
    draft_notices(_problem(), _events(), chat, languages=["en"])
    blob = json.dumps(chat.calls[0]).casefold()
    for banned in ("player", "child", "phone", "email", "payment", "@"):
        assert banned not in blob
    assert "foxes" in blob and "north field" in blob


def test_round_trip() -> None:
    notice = FamilyNotice(language="es", text="Hola Foxes 2026-11-03.", verified=False, missing=["North Field"])
    bundle = FamilyNotices(notices=[notice], notes=["n"])
    assert FamilyNotices.from_dict(json.loads(json.dumps(bundle.to_dict()))) == bundle
    assert FamilyNotice.from_dict(notice.to_dict()) == notice
    assert FamilyNotices(notices=[]).all_verified is False
