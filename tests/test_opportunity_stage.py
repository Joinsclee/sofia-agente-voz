"""Landing a patient's card in a stage — the duplicate that broke every callback.

Every test here exists because of a real defect, not a hypothetical one.

GHL allows exactly ONE opportunity per contact and answers the second one with
`400 OPPORTUNITY_NO_DUPLICATE`. `/update-lead-status` used to call
`create_opportunity` blind, and Retell never sends an `opportunity_id` (its tool
declares only phone/temperature/stage). So the very first returning patient got
a 502 and Sofía read out the human-follow-up line — while the temperature tag
had *already* been written. Half-applied state, plus a message that said nothing
happened. Reproduced live against a real Location on 2026-08-05.

That is the whole point of the outbound worker, too: a no-show already has a
card by definition, so this path was broken for exactly the patients the system
exists to recover.

Two traps make "look, then create" insufficient, and both are covered below:

  * `/opportunities/search` lags writes. A card opened by /book-appointment
    seconds earlier is still invisible there, so the create must also recover
    from the duplicate error using the `meta.existingId` GHL hands back.
  * The card we find may belong to another pipeline entirely. Dragging a
    signed-contract card into a dental stage is worse than the error it avoids.
"""

from __future__ import annotations

import pytest

from app.services import ghl_service
from app.services.ghl_service import GHLAPIError, ensure_opportunity_stage

OURS = "pipeline_sofia"
THEIRS = "pipeline_contratos"
STAGE = "stage_cita_agendada"
CONTACT = "contact_abc"


@pytest.fixture(autouse=True)
def _pipeline(monkeypatch):
    """Sofía's pipeline, so the tests never touch sofia.config.yaml."""
    monkeypatch.setattr(ghl_service, "_default_pipeline_id", lambda: OURS)


def _duplicate_error(existing_id: str) -> GHLAPIError:
    """The exact 400 GHL returns for a second opportunity."""
    return GHLAPIError(
        "POST /opportunities/ -> HTTP 400",
        status_code=400,
        payload={
            "statusCode": 400,
            "message": "Can not create duplicate opportunity for the contact.",
            "code": "OPPORTUNITY_NO_DUPLICATE",
            "meta": {"existingId": existing_id},
        },
    )


# --------------------------------------------------------------------------
# The happy paths
# --------------------------------------------------------------------------


def test_creates_when_the_contact_has_no_card(monkeypatch):
    monkeypatch.setattr(ghl_service, "find_opportunity_for_contact", lambda _cid: None)
    monkeypatch.setattr(ghl_service, "create_opportunity", lambda **kw: {"id": "opp_new"})

    result = ensure_opportunity_stage(CONTACT, STAGE, name="Lead")

    assert result == {"id": "opp_new", "stage_changed": True, "created": True, "foreign_pipeline": None}


def test_moves_the_existing_card_instead_of_duplicating_it(monkeypatch):
    """The defect this module is named after."""
    moved: dict = {}

    monkeypatch.setattr(
        ghl_service,
        "find_opportunity_for_contact",
        lambda _cid: {"id": "opp_existing", "pipeline_id": OURS},
    )
    monkeypatch.setattr(
        ghl_service,
        "update_opportunity_stage",
        lambda opp, stage, **kw: moved.update(opp=opp, stage=stage),
    )

    def _must_not_run(**_kw):
        raise AssertionError("create_opportunity ran on a contact that already had a card")

    monkeypatch.setattr(ghl_service, "create_opportunity", _must_not_run)

    result = ensure_opportunity_stage(CONTACT, STAGE, name="Lead")

    assert moved == {"opp": "opp_existing", "stage": STAGE}
    assert result["stage_changed"] is True
    assert result["created"] is False
    assert result["foreign_pipeline"] is None


# --------------------------------------------------------------------------
# The two traps
# --------------------------------------------------------------------------


def test_never_hijacks_a_card_from_another_pipeline(monkeypatch):
    """A contract or onboarding card must stay exactly where it is."""
    monkeypatch.setattr(
        ghl_service,
        "find_opportunity_for_contact",
        lambda _cid: {"id": "opp_contrato", "pipeline_id": THEIRS},
    )

    def _must_not_run(*_a, **_kw):
        raise AssertionError("touched an opportunity belonging to another pipeline")

    monkeypatch.setattr(ghl_service, "update_opportunity_stage", _must_not_run)
    monkeypatch.setattr(ghl_service, "create_opportunity", _must_not_run)

    result = ensure_opportunity_stage(CONTACT, STAGE, name="Lead")

    assert result["foreign_pipeline"] == THEIRS
    assert result["stage_changed"] is False
    assert result["created"] is False
    assert result["id"] == "opp_contrato"


def test_recovers_when_the_search_index_lags_behind_the_write(monkeypatch):
    """Search says there is no card; GHL says there is. GHL wins."""
    moved: dict = {}

    monkeypatch.setattr(ghl_service, "find_opportunity_for_contact", lambda _cid: None)
    monkeypatch.setattr(
        ghl_service,
        "create_opportunity",
        lambda **_kw: (_ for _ in ()).throw(_duplicate_error("opp_hidden")),
    )
    monkeypatch.setattr(
        ghl_service,
        "get_opportunity",
        lambda opp: {"id": opp, "pipeline_id": OURS},
    )
    monkeypatch.setattr(
        ghl_service,
        "update_opportunity_stage",
        lambda opp, stage, **kw: moved.update(opp=opp, stage=stage),
    )

    result = ensure_opportunity_stage(CONTACT, STAGE, name="Lead")

    assert moved == {"opp": "opp_hidden", "stage": STAGE}
    assert result["id"] == "opp_hidden"
    assert result["stage_changed"] is True
    assert result["created"] is False


def test_lagging_search_plus_foreign_pipeline_still_touches_nothing(monkeypatch):
    """Both traps at once: the hidden card belongs to somebody else."""
    monkeypatch.setattr(ghl_service, "find_opportunity_for_contact", lambda _cid: None)
    monkeypatch.setattr(
        ghl_service,
        "create_opportunity",
        lambda **_kw: (_ for _ in ()).throw(_duplicate_error("opp_hidden")),
    )
    monkeypatch.setattr(ghl_service, "get_opportunity", lambda opp: {"id": opp, "pipeline_id": THEIRS})

    def _must_not_run(*_a, **_kw):
        raise AssertionError("moved a foreign card recovered from the duplicate error")

    monkeypatch.setattr(ghl_service, "update_opportunity_stage", _must_not_run)

    result = ensure_opportunity_stage(CONTACT, STAGE, name="Lead")

    assert result["foreign_pipeline"] == THEIRS
    assert result["stage_changed"] is False


# --------------------------------------------------------------------------
# What must still blow up
# --------------------------------------------------------------------------


def test_an_unrelated_400_is_not_swallowed(monkeypatch):
    """Only the duplicate error is recoverable. Everything else must surface."""
    monkeypatch.setattr(ghl_service, "find_opportunity_for_contact", lambda _cid: None)
    boom = GHLAPIError(
        "POST /opportunities/ -> HTTP 400",
        status_code=400,
        payload={"statusCode": 400, "code": "INVALID_STAGE", "message": "stage does not exist"},
    )
    monkeypatch.setattr(
        ghl_service,
        "create_opportunity",
        lambda **_kw: (_ for _ in ()).throw(boom),
    )

    with pytest.raises(GHLAPIError):
        ensure_opportunity_stage(CONTACT, STAGE, name="Lead")
