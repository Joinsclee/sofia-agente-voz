"""Regression tests for the second-audit fixes.

- _resolve_contact_id must resolve to the SAME contact the in-call tools wrote
  to (mirroring phone_of_record), never the clinic's own line. (finding #1)
- The outbound cooldown timestamp must round-trip through GHL's DATE field as
  epoch milliseconds, and older shapes must still parse. (finding #2)
- A renamed custom-field key must degrade one field, not drop the whole
  post-call batch. (finding #4)
"""
from __future__ import annotations

import app.main as main
import app.worker as worker
from app.services import ghl_service as ghl

LAST_KEY = worker._cfg("tracking_fields.last_attempt", "contact.ultimo_intento_outbound")


# --------------------------------------------------------------------------
# #1 — _resolve_contact_id resolves to the patient, never the clinic line
# --------------------------------------------------------------------------


def _echo_upsert(monkeypatch):
    """upsert_contact echoes the phone it was handed, so tests see the choice."""
    monkeypatch.setattr(main.ghl, "upsert_contact", lambda phone, **kw: {"id": f"UPSERT::{phone}"})


def test_outbound_uses_metadata_contact_id(monkeypatch):
    _echo_upsert(monkeypatch)
    call = {
        "direction": "outbound",
        "from_number": "+14246781756",  # the clinic's Twilio line
        "to_number": "+573001112233",  # the patient we dialled
        "metadata": {"contact_id": "CID_META"},
        "transcript_with_tool_calls": [],
    }
    # The worker put the real contact id in metadata; trust it, never upsert the line.
    assert main._resolve_contact_id(call) == "CID_META"


def test_outbound_without_metadata_uses_patient_not_clinic_line(monkeypatch):
    _echo_upsert(monkeypatch)
    call = {
        "direction": "outbound",
        "from_number": "+14246781756",  # clinic line — must NOT be chosen
        "to_number": "+573001112233",  # patient
        "transcript_with_tool_calls": [],
    }
    assert main._resolve_contact_id(call) == "UPSERT::+573001112233"


def test_inbound_resolves_to_caller_number(monkeypatch):
    _echo_upsert(monkeypatch)
    call = {
        "direction": "inbound",
        "from_number": "+573009998877",  # the patient's caller ID
        "to_number": "+14246781756",
        "transcript_with_tool_calls": [],
    }
    assert main._resolve_contact_id(call) == "UPSERT::+573009998877"


def test_web_call_falls_back_to_tool_phone(monkeypatch):
    _echo_upsert(monkeypatch)
    call = {
        "direction": "web_call",
        "from_number": None,
        "to_number": None,
        "transcript_with_tool_calls": [
            {
                "role": "tool_call_invocation",
                "name": "book_appointment",
                "arguments": '{"phone": "+573005556677"}',
            }
        ],
    }
    assert main._resolve_contact_id(call) == "UPSERT::+573005556677"


def test_no_identifiable_phone_returns_none(monkeypatch):
    _echo_upsert(monkeypatch)
    call = {"direction": "web_call", "from_number": None, "to_number": None,
            "transcript_with_tool_calls": []}
    assert main._resolve_contact_id(call) is None


# --------------------------------------------------------------------------
# #2 — outbound cooldown timestamp round-trips as epoch milliseconds
# --------------------------------------------------------------------------


def test_last_attempt_epoch_ms_round_trips():
    now = worker.now_local()
    ms = int(now.timestamp() * 1000)  # exactly what record_attempt writes
    parsed = worker._parse_last_attempt({LAST_KEY: ms})
    assert parsed is not None
    assert abs((parsed - now).total_seconds()) < 1


def test_last_attempt_accepts_string_and_seconds_and_iso():
    now = worker.now_local()
    assert worker._parse_last_attempt({LAST_KEY: str(int(now.timestamp() * 1000))}) is not None
    assert worker._parse_last_attempt({LAST_KEY: int(now.timestamp())}) is not None
    iso = worker._parse_last_attempt({LAST_KEY: now.isoformat()})
    assert iso is not None and abs((iso - now).total_seconds()) < 1


def test_last_attempt_empty_is_none():
    assert worker._parse_last_attempt({LAST_KEY: ""}) is None
    assert worker._parse_last_attempt({LAST_KEY: None}) is None
    assert worker._parse_last_attempt({LAST_KEY: "not-a-date"}) is None


# --------------------------------------------------------------------------
# #4 — a renamed custom-field key degrades one field, not the whole batch
# --------------------------------------------------------------------------


def test_custom_fields_strict_false_keeps_known(monkeypatch):
    monkeypatch.setattr(ghl, "custom_field_ids", lambda: {"known_a": "ID_A", "known_b": "ID_B"})
    payload = ghl._custom_fields_payload({"known_a": 1, "unknown_x": 2, "known_b": 3}, strict=False)
    assert sorted(p["id"] for p in payload) == ["ID_A", "ID_B"]


def test_custom_fields_strict_true_raises(monkeypatch):
    monkeypatch.setattr(ghl, "custom_field_ids", lambda: {"known_a": "ID_A"})
    import pytest

    with pytest.raises(ghl.GHLError):
        ghl._custom_fields_payload({"known_a": 1, "unknown_x": 2}, strict=True)
