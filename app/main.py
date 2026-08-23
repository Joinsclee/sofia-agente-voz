"""FastAPI on Modal — the HTTP tools Retell calls while Sofía is on the phone.

Every endpoint is a thin handler. The business logic lives in
`app/services/`, so the dashboard can reuse it later without duplicating a
single rule (CLAUDE.md §8).

Response envelope, identical on every endpoint so a frontend can rely on it:

    {"ok": true,  "data": {...},  "message": "..."}       # 2xx
    {"ok": false, "error": {"code": "...", "detail": "..."}, "message": "..."}   # 4xx/5xx

`message` is the line Sofía can say out loud. `data` is for the dashboard.

THE RULE THAT DOES NOT BEND: /book-appointment never answers 200 with a
booking that did not happen. If GHL fails, it returns an error status and the
human-follow-up line, and Sofía offers a callback instead of confirming.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field, model_validator

from app.auth import allowed_origins
from app.dashboard_api import router as dashboard_router
from app.services import anthropic_service
from app.services import ghl_service as ghl
from app.services import ghl_read_service as ghl_read
from app.services.call_parsing import phone_from_tool_calls, transcript_from
from app.services.ghl_service import (
    GHLAPIError,
    GHLBookingError,
    GHLConfigError,
    GHLError,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s | %(message)s")
LOG = logging.getLogger("sofia.api")

MODAL_APP_NAME = "agente-voz-ghl"
MODAL_SECRET_NAME = "agente-voz-credentials"

# Voice constraint, not a technical one: a caller cannot hold twelve options in
# their head. Sofía offers a few and the prompt decides how to phrase them.
MAX_SPOKEN_OPTIONS = 6
DEFAULT_DAYS_AHEAD = 7

# Minimum lead time before a slot may be offered. Guards against offering an
# appointment the patient could not physically reach. Override in
# sofia.config.yaml with `business.min_notice_minutes`.
DEFAULT_MIN_NOTICE_MINUTES = 60

# Fallback if sofia.config.yaml has not been customized yet.
FALLBACK_HUMAN_FOLLOWUP = (
    "Déjame confirmártelo, en un momento te contacta una persona del equipo"
)


# --------------------------------------------------------------------------
# Response envelope
# --------------------------------------------------------------------------


def _human_followup_message() -> str:
    """The honest line Sofía says when GHL is not answering. Never a fake confirmation."""
    return str(ghl.config_value("crm.rules.failure_message", FALLBACK_HUMAN_FOLLOWUP))


def ok(data: Mapping[str, Any] | None = None, message: str = "") -> JSONResponse:
    return JSONResponse(status_code=200, content={"ok": True, "data": dict(data or {}), "message": message})


def fail(
    *,
    status: int,
    code: str,
    detail: str,
    message: str | None = None,
    data: Mapping[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "ok": False,
        "error": {"code": code, "detail": detail},
        "message": message or _human_followup_message(),
    }
    if data:
        body["data"] = dict(data)
    return JSONResponse(status_code=status, content=body)


def _business_tz() -> ZoneInfo:
    return ZoneInfo(str(ghl.config_value("business.timezone", "America/Cancun")))


# --------------------------------------------------------------------------
# Spoken date/time — computed here, not by the model
#
# The LLM has no clock and no calendar. Handed a bare ISO timestamp it cannot
# tell "hoy" from "mañana", and it will confidently say the wrong one out loud.
# The backend knows the clinic's timezone and the current time, so it does the
# conversion and the model only reads the label back.
# --------------------------------------------------------------------------

_WEEKDAYS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_HOURS_ES = {
    1: "una", 2: "dos", 3: "tres", 4: "cuatro", 5: "cinco", 6: "seis",
    7: "siete", 8: "ocho", 9: "nueve", 10: "diez", 11: "once", 12: "doce",
}


def _spoken_time(moment: datetime) -> str:
    """`16:30` -> `las cuatro y media de la tarde`."""
    hour24 = moment.hour
    hour12 = hour24 % 12 or 12
    article = "la" if hour12 == 1 else "las"

    if moment.minute == 0:
        minutes = ""
    elif moment.minute == 15:
        minutes = " y cuarto"
    elif moment.minute == 30:
        minutes = " y media"
    else:
        minutes = f" y {moment.minute}"

    if hour24 < 12:
        period = "de la mañana"
    elif hour24 == 12:
        period = "del día"  # "las doce del día", not "de la tarde"
    elif hour24 < 19:
        period = "de la tarde"
    else:
        period = "de la noche"

    return f"{article} {_HOURS_ES[hour12]}{minutes} {period}"


def _spoken_day(moment: datetime, now: datetime) -> str:
    """`hoy`, `mañana`, or `el martes 21` — relative to the clinic's today."""
    delta_days = (moment.date() - now.date()).days
    if delta_days == 0:
        return "hoy"
    if delta_days == 1:
        return "mañana"
    return f"el {_WEEKDAYS_ES[moment.weekday()]} {moment.day}"


def _spoken_label(moment: datetime, now: datetime) -> str:
    """The whole phrase Sofía can say without doing any date math herself."""
    return f"{_spoken_day(moment, now)} a {_spoken_time(moment)}"


# --------------------------------------------------------------------------
# Request models
# --------------------------------------------------------------------------


class RetellToolRequest(BaseModel):
    """Base model that accepts either flat arguments or Retell's nested envelope.

    Retell has shipped both shapes for custom tools: sometimes the arguments
    arrive at the top level, sometimes wrapped in `args` alongside `call`
    metadata. Accepting both here means a Retell config change never silently
    breaks a live agent.
    """

    call_id: str | None = None
    # The line the call is physically on, straight from the telephony layer —
    # NOT transcribed by the LLM, so it cannot be corrupted the way a spoken
    # phone number can. This is what makes the phantom-number bug fixable.
    from_number: str | None = None
    to_number: str | None = None
    direction: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, data: Any) -> Any:
        if not isinstance(data, Mapping):
            return data
        payload = {k: v for k, v in data.items() if k not in {"args", "call", "name"}}
        args = data.get("args")
        if isinstance(args, Mapping):
            payload.update(args)
        call = data.get("call")
        if isinstance(call, Mapping):
            if not payload.get("call_id"):
                payload["call_id"] = call.get("call_id") or call.get("callId")
            # Only the envelope carries these; never let a spoofed arg win.
            for key in ("from_number", "to_number", "direction"):
                if call.get(key) is not None:
                    payload[key] = call.get(key)
        return payload


class CreateLeadRequest(RetellToolRequest):
    phone: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    reason: str | None = Field(default=None, description="Motivo de la llamada")
    tags: list[str] | None = None


class CheckAvailabilityRequest(RetellToolRequest):
    start_date: str | None = Field(default=None, description="YYYY-MM-DD; por defecto hoy")
    days_ahead: int = Field(default=DEFAULT_DAYS_AHEAD, ge=1, le=ghl.FREE_SLOTS_MAX_DAYS)
    max_options: int = Field(default=MAX_SPOKEN_OPTIONS, ge=1, le=20)
    calendar_id: str | None = None


class BookAppointmentRequest(RetellToolRequest):
    phone: str | None = None
    start_time: str = Field(description="ISO local de la clínica, ej. 2026-07-21T16:00:00")
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    reason: str | None = None
    treatment: str | None = None
    urgency: str | None = Field(default=None, description="urgente | normal | baja")
    temperature: str | None = Field(default=None, description="hot | warm | cold")
    duration_minutes: int | None = Field(default=None, ge=5, le=240)


class UpdateLeadStatusRequest(RetellToolRequest):
    contact_id: str | None = None
    phone: str | None = None
    temperature: str | None = Field(default=None, description="hot | warm | cold")
    stage: str | None = Field(default=None, description="clave de crm.pipeline_stages")
    stage_id: str | None = None
    opportunity_id: str | None = None
    opportunity_name: str | None = None


class GetAppointmentRequest(RetellToolRequest):
    phone: str | None = None


class CancelAppointmentRequest(RetellToolRequest):
    phone: str | None = None


class RescheduleAppointmentRequest(RetellToolRequest):
    phone: str | None = None
    new_start_time: str = Field(
        description="El `iso` del nuevo horario, copiado tal cual de check_availability."
    )


# --------------------------------------------------------------------------
# Phone number of record — the one write path that must never be corrupted
# --------------------------------------------------------------------------


def _same_line(a: str | None, b: str | None) -> bool:
    """True when two raw numbers are the same line, whatever their formatting."""
    if not a or not b:
        return False
    try:
        return ghl.to_e164(a) == ghl.to_e164(b)
    except ValueError:
        return "".join(filter(str.isdigit, a)) == "".join(filter(str.isdigit, b))


def _is_forwarding_line(from_number: str | None, to_number: str | None) -> bool:
    """True when the inbound caller ID is a line that forwards to us, not a patient.

    Colombia has no fixed-line portability, so a clinic keeps its published
    number by FORWARDING it to ours. Some carriers pass the caller's own number
    across that hop and some replace it with the forwarding line, and which one
    you get is not knowable from here.

    Two shapes, and only checking the first is a trap:

      * The clinic forwards its number straight to the same number being
        dialled. Then `from == to` and the comparison alone catches it.
      * The usual production shape: the clinic's published 604 forwards to a
        DIFFERENT number we provisioned. The rewritten caller ID is the
        published 604, `to_number` is the new line, they do not match, and the
        comparison never fires. The corrupted line then wins over the spoken
        digits and every patient is written against the clinic's switchboard.

    So the forwarding numbers are declared in `business.forwarding_source_numbers`
    and treated as identifying nobody. Declared and not guessed: a number that
    forwards to us is a fact of the install, not something to infer at runtime.
    """
    if not from_number:
        return False
    if _same_line(from_number, to_number):
        return True
    declared = ghl.config_value("business.forwarding_source_numbers", []) or []
    return any(_same_line(from_number, str(n)) for n in declared)


def _line_number(req: RetellToolRequest) -> str | None:
    """The patient's number as the telephony layer sees it, or None.

    Inbound: the caller ID they are dialling from. Outbound: the number we
    dialled — which the worker itself chose from GHL, so it is correct by
    construction. Both come off the SIP signalling, never off speech-to-text.
    A web/test call with no PSTN leg carries neither, hence None.

    THE FORWARDING CASE. A clinic that keeps its published number forwards it to
    ours instead of porting it. Some carriers preserve the original caller ID
    through that hop and some replace it with the forwarding line, and which one
    you get is not knowable from here. If it is replaced, `from_number` is the
    CLINIC's own number, and since the line outranks the spoken digits every
    patient would be written against it. `upsert_contact` is idempotent by
    phone, so they would not pile up as duplicates: they would collapse into a
    SINGLE contact carrying every appointment, and the clinic could never call
    anyone back. Silent, and worse the better the system works.

    So a line that equals the number being dialled identifies nobody. Return
    None and let the spoken number take over, which is exactly what it is for.
    """
    direction = (req.direction or "").lower()
    if direction == "outbound":
        return req.to_number
    if direction == "inbound":
        if _is_forwarding_line(req.from_number, req.to_number):
            LOG.warning(
                "call=%s: caller ID %s is a forwarding line, not a patient. The "
                "carrier rewrote it on the hop — falling back to the spoken number.",
                req.call_id,
                req.from_number,
            )
            return None
        return req.from_number
    return None


def phone_of_record(req: RetellToolRequest, spoken_phone: str | None) -> tuple[str, str]:
    """The phone to actually write to GHL, and where it came from.

    This is the fix for the phantom-number bug. Sofía once confirmed a caller's
    digits correctly out loud and then handed book_appointment a DIFFERENT
    number — the LLM re-transcribed its own confirmation and corrupted it. The
    appointment booked against a number that does not exist; the patient hung up
    happy and the clinic could never reach them.

    The telephony line is ground truth: the caller is provably on it right now
    (inbound) or we just dialled it from the CRM (outbound). So the line wins.
    The spoken number is only used when there is no line at all (a web test), and
    a disagreement between the two is logged loudly — it is the exact signature
    of the bug, and worth seeing even now that it can no longer do damage.

    Raises ValueError when neither source yields a valid number, so the caller
    can fail honestly instead of booking against garbage.
    """
    line = _line_number(req)
    if line:
        line_e164 = ghl.to_e164(line)
        if spoken_phone:
            try:
                spoken_e164 = ghl.to_e164(spoken_phone)
            except ValueError:
                spoken_e164 = None
            if spoken_e164 and spoken_e164 != line_e164:
                LOG.warning(
                    "Phone mismatch on call=%s: model passed %s but the line is %s — trusting the line",
                    req.call_id,
                    spoken_e164,
                    line_e164,
                )
        return line_e164, "line"

    # No PSTN leg (web/test call): the spoken number is all there is. It still
    # goes through to_e164, which raises on anything that is not valid E.164.
    return ghl.to_e164(spoken_phone or ""), "spoken"


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

web_app = FastAPI(
    title="Sofía · agente de voz",
    description="Tools que Retell llama en vivo. GoHighLevel es la única fuente de la verdad.",
    version="0.1.0",
)

# The dashboard's read endpoints, under /dashboard and behind the shared token.
# They live in their own module so nothing added for the panel can reach the
# action endpoints Retell depends on mid-call.
web_app.include_router(dashboard_router)

# CORS stays closed unless an origin is named explicitly. The panel proxies
# through its own server, so the browser never calls this API cross-origin and
# the default of "no origins" is the correct one for a normal deployment.
_origins = allowed_origins()
if _origins:
    from fastapi.middleware.cors import CORSMiddleware

    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )
    LOG.info("CORS enabled for %s", _origins)


@web_app.exception_handler(GHLError)
async def _ghl_error_handler(_: Request, exc: GHLError) -> JSONResponse:
    """One place that turns a services-layer failure into an honest HTTP answer."""
    if isinstance(exc, GHLConfigError):
        # Our misconfiguration, not GHL's fault. Loud on purpose.
        LOG.error("Configuration error: %s", exc)
        return fail(status=500, code="config_error", detail=str(exc))
    if isinstance(exc, GHLBookingError):
        LOG.error("Booking failed: %s", exc)
        return fail(status=502, code="booking_failed", detail=str(exc))
    if isinstance(exc, GHLAPIError):
        LOG.error("GHL API error: %s", exc)
        return fail(status=502, code="ghl_unavailable", detail=str(exc))
    LOG.error("Unexpected GHL layer error: %s", exc)
    return fail(status=502, code="ghl_error", detail=str(exc))


@web_app.exception_handler(ValueError)
async def _value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
    """Bad input — a malformed phone, a range too wide. The caller can fix it."""
    LOG.warning("Invalid input: %s", exc)
    return fail(
        status=400,
        code="invalid_input",
        detail=str(exc),
        message="No pude tomar ese dato, ¿me lo confirmas otra vez?",
    )


@web_app.exception_handler(RequestValidationError)
async def _validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    """Keep FastAPI's 422 inside our envelope.

    The default body is `{"detail": [...]}`, a different shape from every other
    answer this API gives. Retell would hand Sofía something she cannot read,
    and the dashboard would need a second parser for one status code.
    """
    problems = "; ".join(
        f"{'.'.join(str(p) for p in err.get('loc', []) if p != 'body')}: {err.get('msg')}"
        for err in exc.errors()
    )
    LOG.warning("Request validation failed: %s", problems)
    return fail(
        status=400,
        code="invalid_input",
        detail=problems or "request body failed validation",
        message="Me falta un dato para continuar, ¿me lo repites?",
    )


@web_app.exception_handler(Exception)
async def _unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Last resort: Sofía must always receive a parseable answer, never an HTML 500.

    The detail is deliberately generic — an internal traceback has no business
    reaching Retell — but the full error goes to the logs.
    """
    LOG.exception("Unhandled error: %s", exc)
    return fail(
        status=500,
        code="internal_error",
        detail="Unexpected backend error; see logs.",
    )


# ==========================================================================
# POST /create-lead
# ==========================================================================


@web_app.post("/create-lead")
async def create_lead(payload: CreateLeadRequest) -> JSONResponse:
    """Registers the caller in the CRM. Idempotent by phone — calling twice never duplicates."""
    custom_fields = {}
    reason_key = ghl.config_value("crm.custom_fields.reason_for_visit")
    if payload.reason and reason_key:
        custom_fields[reason_key] = payload.reason

    try:
        phone, _ = phone_of_record(payload, payload.phone)
    except ValueError as exc:
        return fail(
            status=400,
            code="invalid_input",
            detail=f"No usable phone number: {exc}",
            message="No pude tomar tu número. ¿Me lo repites, por favor?",
        )

    contact = ghl.upsert_contact(
        phone=phone,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        tags=payload.tags,
        custom_fields=custom_fields or None,
    )

    LOG.info("create-lead call=%s contact=%s new=%s", payload.call_id, contact["id"], contact["is_new"])
    return ok(
        {
            "contact_id": contact["id"],
            "is_new": contact["is_new"],
            "phone": contact["phone"],
        },
        message="Listo, ya tengo tus datos.",
    )


# ==========================================================================
# POST /check-availability
# ==========================================================================


@web_app.post("/check-availability")
async def check_availability(payload: CheckAvailabilityRequest) -> JSONResponse:
    """Reads the real calendar and returns the openings Sofía is allowed to offer."""
    tz = _business_tz()
    now = datetime.now(tz)
    min_notice = int(ghl.config_value("business.min_notice_minutes", DEFAULT_MIN_NOTICE_MINUTES))
    cutoff = now + timedelta(minutes=min_notice)

    if payload.start_date:
        start = datetime.fromisoformat(payload.start_date).replace(tzinfo=tz)
    else:
        start = now
    # Never offer a slot that has already passed, nor one so close the patient
    # could not physically get there. Asking GHL from midnight returns the whole
    # day, including the hours that are already gone.
    start = max(start, cutoff)
    end = start + timedelta(days=payload.days_ahead)

    slots_by_date = ghl.get_free_slots(start, end, calendar_id=payload.calendar_id)

    # Belt and braces: GHL buckets by day, so a same-day query can still hand
    # back this morning's slots. Drop anything at or before the cutoff.
    future_by_date: dict[str, list[str]] = {}
    dropped = 0
    for date, slots in slots_by_date.items():
        kept = []
        for slot in slots:
            try:
                moment = datetime.fromisoformat(slot)
            except ValueError:
                continue
            if moment > cutoff:
                kept.append(slot)
            else:
                dropped += 1
        if kept:
            future_by_date[date] = kept

    # Flatten in chronological order and cap it — this is what Sofía reads out.
    options: list[dict[str, str]] = []
    for date in sorted(future_by_date):
        for slot in future_by_date[date]:
            options.append({"iso": slot, "label": _spoken_label(datetime.fromisoformat(slot), now)})
            if len(options) >= payload.max_options:
                break
        if len(options) >= payload.max_options:
            break

    if not options:
        LOG.info("check-availability call=%s no future slots (dropped %s past)", payload.call_id, dropped)
        return ok(
            {
                "slots_by_date": {},
                "options": [],
                "has_availability": False,
                "current_datetime": now.isoformat(),
            },
            message="No encontré espacios en ese rango. ¿Buscamos en otra fecha?",
        )

    LOG.info(
        "check-availability call=%s days=%s options=%s dropped_past=%s",
        payload.call_id,
        len(future_by_date),
        len(options),
        dropped,
    )
    return ok(
        {
            "slots_by_date": future_by_date,
            "options": options,
            "has_availability": True,
            "timezone": str(tz),
            # The model has no clock of its own. Without this it cannot tell
            # "hoy" from "mañana" and will narrate the wrong day out loud.
            "current_datetime": now.isoformat(),
        },
        message="Tengo estos horarios disponibles.",
    )


# ==========================================================================
# POST /book-appointment
# ==========================================================================


@web_app.post("/book-appointment")
async def book_appointment(payload: BookAppointmentRequest) -> JSONResponse:
    """Books the valoración: contact, appointment and pipeline card, in that order.

    The order is deliberate. The appointment is the promise made to the patient,
    so nothing is confirmed until GHL has actually created it.
    """
    # --- Step 1: the contact. Without it there is nothing to book against. ---
    custom_fields = {}
    reason_key = ghl.config_value("crm.custom_fields.reason_for_visit")
    if payload.reason and reason_key:
        custom_fields[reason_key] = payload.reason

    # The number of record comes from the telephony line, not the model's
    # transcription of it — see phone_of_record. This is the guard that stops a
    # confirmed-but-corrupted number from booking a phantom appointment.
    try:
        phone, _ = phone_of_record(payload, payload.phone)
    except ValueError as exc:
        return fail(
            status=400,
            code="invalid_input",
            detail=f"No usable phone number: {exc}",
            message="No pude tomar tu número para agendar. ¿Me lo confirmas, por favor?",
        )

    # The temperature tag is NOT passed to upsert_contact: that write is additive
    # and would stack on top of whatever tag is already there. It goes through
    # set_temperature_tag below, once the contact exists.
    contact = ghl.upsert_contact(
        phone=phone,
        first_name=payload.first_name,
        last_name=payload.last_name,
        email=payload.email,
        custom_fields=custom_fields or None,
    )

    if payload.temperature:
        try:
            ghl.set_temperature_tag(contact["id"], payload.temperature)
        except (ghl.GHLError, ValueError) as exc:
            # A wrong tag must never cost the patient the appointment.
            LOG.warning("Could not set temperature on contact %s: %s", contact["id"], exc)

    # --- Step 2: the appointment. This is the promise. It either lands or we say so. ---
    # book_or_reschedule guards against the model calling this twice in one turn
    # (it books, the patient gives their name, and it "completes" the booking
    # again): it reuses the patient's OWN future appointment — same time keeps it,
    # different time moves it — instead of creating a second or colliding with it.
    duration = payload.duration_minutes or ghl.DEFAULT_APPOINTMENT_MINUTES
    appointment = ghl.book_or_reschedule(
        contact_id=contact["id"],
        start=payload.start_time,
        duration_minutes=duration,
        calendar_id=None,
    )
    # If we reach this line the slot is really taken in the clinic's calendar.
    # GHLBookingError would have been raised otherwise and handled above.

    # --- Step 3: the pipeline card. Bookkeeping, not a promise. ---
    #
    # A failure here must NOT be reported as a failed booking. The appointment
    # exists; telling the patient it did not would send them elsewhere while the
    # slot stays blocked — and risks a second booking on top of the first.
    # Sofía confirms truthfully; the gap is flagged for a human to close.
    warnings: list[str] = []
    opportunity_id = None
    treatment = payload.treatment or str(ghl.config_value("knowledge_base.valoracion.name", "Cita de valoración"))
    display_name = " ".join(filter(None, [payload.first_name, payload.last_name])) or phone

    try:
        opportunity = ghl.create_opportunity(
            contact_id=contact["id"],
            name=f"{display_name} · {treatment}",
        )
        opportunity_id = opportunity["id"]
    except GHLError as exc:
        LOG.error("Appointment %s booked but opportunity failed: %s", appointment["id"], exc)
        warnings.append("opportunity_not_created")

    LOG.info(
        "book-appointment call=%s contact=%s appointment=%s start=%s warnings=%s",
        payload.call_id,
        contact["id"],
        appointment["id"],
        appointment["start_time"],
        warnings,
    )
    # If the contact already had this exact appointment (double-call) or we moved
    # an existing one, tell the model so it does not announce a second booking.
    nota = None
    if appointment.get("already_booked"):
        nota = "Este contacto YA tenía esta cita a esta misma hora. NO la anuncies otra vez ni pidas disculpas: confírmala tal cual y seguí."
    elif appointment.get("rescheduled"):
        nota = "El contacto ya tenía una cita y la MOVISTE a la hora nueva (no creaste una segunda). Confírmale el nuevo horario una sola vez."

    return ok(
        {
            "contact_id": contact["id"],
            "appointment_id": appointment["id"],
            "opportunity_id": opportunity_id,
            "start_time": appointment["start_time"],
            "end_time": appointment["end_time"],
            "status": appointment["status"],
            "urgency": payload.urgency,
            "already_booked": bool(appointment.get("already_booked")),
            "rescheduled": bool(appointment.get("rescheduled")),
            "nota": nota,
            "warnings": warnings,
        },
        message="Tu cita quedó agendada.",
    )


# ==========================================================================
# POST /get-appointment · /cancel-appointment · /reschedule-appointment
#
# The caller is identified by their LINE of record (phone_of_record), never by
# a number the model transcribed — so nobody can touch someone else's cita.
# ==========================================================================


def _resolve_caller_contact(payload: RetellToolRequest, spoken_phone: str | None) -> str | None:
    """The caller's contact id from their line of record, or None if not a patient.

    A READ path: use a lookup that does NOT create. upsert_contact would mint a
    phantom contact for every wrong number or spam caller asking "do I have an
    appointment?". None flows through find_future_appointment -> "no appointment",
    which is exactly right for a number the clinic never registered.
    """
    phone, _ = phone_of_record(payload, spoken_phone)
    contact = ghl_read.find_contact_by_phone(phone)
    return contact.get("id") if contact else None


@web_app.post("/get-appointment")
async def get_appointment(payload: GetAppointmentRequest) -> JSONResponse:
    """Finds the caller's upcoming appointment so Sofía can read it back."""
    contact_id = _resolve_caller_contact(payload, payload.phone)
    appt = ghl.find_future_appointment(contact_id)
    if not appt:
        return ok(
            {"has_appointment": False, "contact_id": contact_id},
            message="No encontré una cita próxima a tu nombre.",
        )
    label = _spoken_label(appt["start"], datetime.now(_business_tz()))
    return ok(
        {
            "has_appointment": True,
            "appointment_id": appt["id"],
            "start_time": appt["start"].isoformat(),
            "label": label,
            "contact_id": contact_id,
        },
        message="Encontré tu cita.",
    )


@web_app.post("/cancel-appointment")
async def cancel_appointment(payload: CancelAppointmentRequest) -> JSONResponse:
    """Cancels the caller's upcoming appointment. Frees the slot; keeps the record.

    GHLBookingError propagates to the GHLError handler, so a failed cancel is
    reported honestly and Sofía never says "quedó cancelada" when it did not.
    """
    contact_id = _resolve_caller_contact(payload, payload.phone)
    appt = ghl.find_future_appointment(contact_id)
    if not appt:
        return ok(
            {"cancelled": False, "has_appointment": False},
            message="No encontré una cita próxima a tu nombre para cancelar.",
        )
    label = _spoken_label(appt["start"], datetime.now(_business_tz()))
    ghl.cancel_appointment(appt["id"])
    return ok(
        {"cancelled": True, "appointment_id": appt["id"], "label": label},
        message="Tu cita quedó cancelada.",
    )


@web_app.post("/reschedule-appointment")
async def reschedule_appointment(payload: RescheduleAppointmentRequest) -> JSONResponse:
    """Moves the caller's upcoming appointment to a new time from check_availability."""
    contact_id = _resolve_caller_contact(payload, payload.phone)
    appt = ghl.find_future_appointment(contact_id)
    if not appt:
        return ok(
            {"rescheduled": False, "has_appointment": False},
            message="No encontré una cita próxima a tu nombre para cambiar.",
        )
    moved = ghl.reschedule_appointment(appt["id"], payload.new_start_time)
    label = _spoken_label(
        datetime.fromisoformat(moved["start_time"]), datetime.now(_business_tz())
    )
    return ok(
        {"rescheduled": True, "appointment_id": appt["id"], "label": label},
        message="Tu cita quedó reprogramada.",
    )


# ==========================================================================
# POST /update-lead-status
# ==========================================================================


@web_app.post("/update-lead-status")
async def update_lead_status(payload: UpdateLeadStatusRequest) -> JSONResponse:
    """Sets the lead temperature and moves the patient's card along the pipeline."""
    # Prefer the telephony line over a spoken phone here too: a corrupted number
    # would upsert a brand-new phantom contact and tag THAT instead of the
    # patient on the call.
    lookup_phone = None
    if not payload.contact_id:
        try:
            lookup_phone, _ = phone_of_record(payload, payload.phone)
        except ValueError:
            lookup_phone = None

    if not payload.contact_id and not lookup_phone:
        return fail(
            status=400,
            code="invalid_input",
            detail="Either contact_id or a usable phone is required",
            message="Me falta identificar al paciente.",
        )

    # upsert is idempotent by phone, so it doubles as a lookup with no extra endpoint.
    contact_id = payload.contact_id or ghl.upsert_contact(phone=lookup_phone)["id"]

    applied_tags: list[str] = []
    if payload.temperature:
        allowed = ghl.config_value("crm.tags.temperature", ["hot", "warm", "cold"])
        if payload.temperature not in allowed:
            return fail(
                status=400,
                code="invalid_input",
                detail=f"temperature must be one of {allowed}, got `{payload.temperature}`",
                message="No reconocí esa temperatura de lead.",
            )
        # Replaces the sibling temperature tags instead of stacking on them.
        applied_tags = ghl.set_temperature_tag(contact_id, payload.temperature)["tags"]

    # Resolve the stage from the config map so handlers never carry GHL ids.
    stage_id = payload.stage_id
    if not stage_id and payload.stage:
        stage_map = ghl.config_value("crm.pipeline_stages", {}) or {}
        stage_id = stage_map.get(payload.stage)
        if not stage_id:
            return fail(
                status=400,
                code="invalid_input",
                detail=f"stage `{payload.stage}` is not in crm.pipeline_stages ({sorted(stage_map)})",
                message="No reconocí esa etapa del pipeline.",
            )

    opportunity_id = payload.opportunity_id
    stage_changed = False
    warnings: list[str] = []

    if stage_id:
        if opportunity_id:
            # The caller already knows which card it is. Trust it and move it.
            ghl.update_opportunity_stage(opportunity_id, stage_id)
            stage_changed = True
        else:
            # Retell never sends an opportunity_id (the tool only declares
            # phone/temperature/stage), so this is the path every real call takes.
            # ensure_opportunity_stage handles the returning patient, whose card
            # already exists and whom a blind create used to reject with a 400.
            landed = ghl.ensure_opportunity_stage(
                contact_id,
                stage_id,
                name=payload.opportunity_name or f"Lead {contact_id}",
            )
            opportunity_id = landed["id"]
            stage_changed = landed["stage_changed"]
            if landed["foreign_pipeline"]:
                warnings.append(
                    f"El contacto ya tiene una oportunidad en el pipeline {landed['foreign_pipeline']}, "
                    "que no es el de Sofía. No la moví para no alterar otro proceso."
                )

    LOG.info(
        "update-lead-status call=%s contact=%s tags=%s stage=%s",
        payload.call_id,
        contact_id,
        applied_tags,
        stage_id,
    )
    return ok(
        {
            "contact_id": contact_id,
            "tags": applied_tags,
            "opportunity_id": opportunity_id,
            "stage_id": stage_id,
            "stage_changed": stage_changed,
            "warnings": warnings,
        },
        message="Actualicé el estado del paciente.",
    )


# ==========================================================================
# Post-call analysis — runs after the webhook has already answered Retell
# ==========================================================================


def _resolve_contact_id(call: Mapping[str, Any]) -> str | None:
    """Find the GHL contact this call belongs to, without any local state.

    Resolves to the SAME contact the in-call tools wrote to, mirroring
    phone_of_record's line-of-record rules — never the clinic's own number:

    1. Outbound calls carry the CRM contact id the worker dialled, echoed back
       in Retell's `metadata`. Trust it directly (`from_number` here is the
       clinic's Twilio line, not the patient).
    2. Otherwise the telephony line the booking used — direction-aware and
       forwarding-aware (`_line_number` returns None on a forwarding line, so a
       switchboard caller ID never becomes a phantom contact).
    3. Web/test calls with no PSTN leg: the phone Sofía passed to a tool, which
       is exactly what phone_of_record fell back to when it created the contact.
    """
    meta = call.get("metadata")
    if isinstance(meta, Mapping) and meta.get("contact_id"):
        return str(meta["contact_id"])

    req = RetellToolRequest(
        call_id=call.get("call_id"),
        from_number=call.get("from_number"),
        to_number=call.get("to_number"),
        direction=call.get("direction"),
    )
    phone = _line_number(req) or phone_from_tool_calls(call)
    if not phone:
        return None
    try:
        return ghl.upsert_contact(phone=phone)["id"]  # idempotent: resolves, never duplicates
    except (ghl.GHLError, ValueError) as exc:
        LOG.error("Could not resolve contact for phone %s: %s", phone, exc)
        return None


def process_call_ended(call: Mapping[str, Any]) -> None:
    """Analyze the transcript with Claude and write the result onto the GHL contact.

    Runs in the background. Every failure is contained here: Retell already got
    its 200, and a broken analysis must never take down the webhook.
    """
    call_id = call.get("call_id")
    transcript = transcript_from(call)

    if not transcript.strip():
        LOG.warning("call_ended call=%s had no transcript; nothing to analyze", call_id)
        return

    contact_id = _resolve_contact_id(call)
    if not contact_id:
        # No phone was ever captured — a hang-up before qualification. There is
        # no contact to attach anything to, and inventing one would pollute the CRM.
        LOG.warning("call_ended call=%s could not be matched to a contact; skipping write", call_id)
        return

    duration_s = round((call.get("duration_ms") or 0) / 1000) or None

    # --- The analysis. If it fails, the transcript still gets saved. ---
    try:
        analysis = anthropic_service.analyze_call(transcript, call_id=call_id)
    except anthropic_service.AnalysisError as exc:
        LOG.error("Analysis failed for call=%s: %s", call_id, exc)
        try:
            ghl.add_note(contact_id, anthropic_service.format_fallback_note(transcript, str(exc), call_id=call_id))
            LOG.info("Wrote raw-transcript fallback note for call=%s", call_id)
        except ghl.GHLError as note_exc:
            LOG.error("Could not even write the fallback note for call=%s: %s", call_id, note_exc)
        return

    # --- The note: summary + next action, on the contact's file. ---
    try:
        ghl.add_note(contact_id, anthropic_service.format_note(analysis, call_id=call_id, duration_s=duration_s))
    except ghl.GHLError as exc:
        LOG.error("Could not write note for call=%s: %s", call_id, exc)

    # --- The scores: each into its configured custom field. ---
    #
    # Written with strict=False so a single renamed/missing field key degrades
    # that field only, not the whole batch. Note what is absent:
    # contact.notas_clinicas. That field is the doctor's clinical record; model
    # output never goes there.
    field_values = {
        "interes_score": analysis.interes_score,
        "nivel_urgencia": analysis.nivel_urgencia,
        "probabilidad_asistir": analysis.probabilidad_asistir,
        "resumen_llamada": analysis.resumen,
    }
    custom_fields = {}
    for config_key, value in field_values.items():
        field_key = ghl.config_value(f"crm.custom_fields.{config_key}")
        if field_key and value is not None:
            custom_fields[field_key] = value

    if custom_fields:
        try:
            ghl.update_contact_fields(contact_id, custom_fields, strict=False)
        except (ghl.GHLError, ValueError) as exc:
            LOG.error("Could not write custom fields for call=%s: %s", call_id, exc)

    # --- The temperature tag. ---
    # This runs last of the three writers and reads the whole transcript, so its
    # verdict replaces the preliminary one the agent set during the call.
    if analysis.temperatura:
        try:
            ghl.set_temperature_tag(contact_id, analysis.temperatura)
        except (ghl.GHLError, ValueError) as exc:
            LOG.error("Could not tag contact for call=%s: %s", call_id, exc)

    LOG.info(
        "Post-call analysis written call=%s contact=%s fields=%s tag=%s",
        call_id,
        contact_id,
        len(custom_fields),
        analysis.temperatura,
    )


# ==========================================================================
# POST /retell-webhook
# ==========================================================================


@web_app.post("/retell-webhook")
async def retell_webhook(request: Request, background: BackgroundTasks) -> JSONResponse:
    """Receives Retell's call lifecycle events: call_started, call_ended, call_analyzed.

    The signature is verified because this URL is public: without it anyone who
    learns the address could post fabricated calls into the clinic's CRM.

    Today this endpoint records the event and returns fast — Retell retries on a
    slow or failing webhook, and a retry storm during a live call is the last
    thing the backend needs. The Claude post-call analysis hangs off `call_ended`
    and is step 3 of the build order.
    """
    raw_body = await request.body()
    signature = request.headers.get("x-retell-signature", "")
    api_key = (os.environ.get("RETELL_API_KEY") or "").strip()

    if not api_key:
        LOG.error("RETELL_API_KEY missing; cannot verify webhook signature")
        return fail(status=500, code="config_error", detail="RETELL_API_KEY is not configured")

    try:
        from retell.lib import verify as verify_retell_signature

        valid = verify_retell_signature(raw_body.decode("utf-8"), api_key, signature)
    except Exception as exc:  # noqa: BLE001 - never let verification crash the webhook
        LOG.error("Signature verification raised: %s", exc)
        valid = False

    if not valid:
        LOG.warning("Rejected webhook with invalid signature from %s", request.client.host if request.client else "?")
        return fail(status=401, code="invalid_signature", detail="Signature verification failed")

    try:
        body = json.loads(raw_body or b"{}")
    except json.JSONDecodeError as exc:
        return fail(status=400, code="invalid_input", detail=f"Body is not JSON: {exc}")

    event = body.get("event")
    call = body.get("call") or {}
    call_id = call.get("call_id")

    if event == "call_started":
        LOG.info("call_started call=%s from=%s", call_id, call.get("from_number"))
    elif event == "call_ended":
        LOG.info(
            "call_ended call=%s duration=%sms disconnect=%s",
            call_id,
            call.get("duration_ms"),
            call.get("disconnection_reason"),
        )
        # Claude + GHL writes happen after this response is sent. Retell gets its
        # 200 immediately: an analysis that takes 15s must never look like a slow
        # webhook, or Retell starts retrying and we analyze the same call twice.
        background.add_task(process_call_ended, call)
    elif event == "call_analyzed":
        LOG.info("call_analyzed call=%s", call_id)
    else:
        LOG.info("Unhandled Retell event `%s` call=%s", event, call_id)

    return ok({"event": event, "call_id": call_id, "handled": True}, message="")


# ==========================================================================
# GET /health
# ==========================================================================


# --------------------------------------------------------------------------
# Clínica Isis — talking-mascot demo ("Bianca"). SEPARATE from Aurora.
# The page is served here so it is same-origin with the token endpoint below
# (no CORS to start the web call). Scoped to the one Isis agent on purpose.
# --------------------------------------------------------------------------
ISIS_DEMO_AGENT_ID = (
    os.environ.get("RETELL_ISIS_AGENT_ID") or "agent_ae9bce89c54c26f046c9950444"
).strip()
_CORS_ANY = {"Access-Control-Allow-Origin": "*"}


def _demo_html_path(filename: str) -> Path | None:
    """Locate a demo HTML file across local dev and the Modal image layout."""
    from app.services.ghl_service import _REPO_ROOT  # proven to resolve to /root in the image

    candidates = [
        _REPO_ROOT / "demo" / filename,
        Path(__file__).resolve().parents[1] / "demo" / filename,
        Path("/root/demo") / filename,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _serve_demo(filename: str) -> HTMLResponse:
    path = _demo_html_path(filename)
    if not path:
        return HTMLResponse("<h1>Demo no disponible</h1>", status_code=404)
    return HTMLResponse(path.read_text(encoding="utf-8"))


@web_app.get("/mascota")
async def mascota_page() -> HTMLResponse:
    """Clínica Isis talking-mascot demo (Bianca)."""
    return _serve_demo("mascota-bianca.html")


@web_app.get("/checklist")
async def checklist_page() -> HTMLResponse:
    """Clínica Isis surgical-traceability checklist demo."""
    return _serve_demo("checklist-isis.html")


@web_app.get("/chat-interno")
async def chat_interno_page() -> HTMLResponse:
    """Clínica Isis internal per-case communication demo."""
    return _serve_demo("chat-interno-isis.html")


@web_app.get("/mascota-3d")
async def mascota_3d_page() -> HTMLResponse:
    """Clínica Isis 3D talking-avatar demo (Ready Player Me + three.js)."""
    return _serve_demo("mascota-3d.html")


@web_app.options("/isis-web-call")
async def isis_web_call_options() -> JSONResponse:
    return JSONResponse(
        status_code=204,
        content=None,
        headers={**_CORS_ANY, "Access-Control-Allow-Methods": "POST, OPTIONS",
                 "Access-Control-Allow-Headers": "Content-Type"},
    )


@web_app.post("/isis-web-call")
async def isis_web_call() -> JSONResponse:
    """Mint a short-lived Retell web-call token for the Isis demo agent only."""
    from app.services import retell_service as rs

    try:
        call = rs._as_dict(rs._client().call.create_web_call(agent_id=ISIS_DEMO_AGENT_ID))
    except Exception as exc:  # noqa: BLE001 — demo endpoint, fail honestly
        LOG.error("isis-web-call failed: %s", exc)
        return JSONResponse(
            status_code=502,
            content={"ok": False, "error": "retell_web_call_failed", "detail": str(exc)[:200]},
            headers=_CORS_ANY,
        )
    token = call.get("access_token") or call.get("accessToken")
    return JSONResponse(
        status_code=200,
        content={"ok": True, "data": {"access_token": token, "call_id": call.get("call_id")}},
        headers=_CORS_ANY,
    )


@web_app.get("/health")
async def health() -> JSONResponse:
    """Says whether the backend is up and configured. Deliberately does not call GHL.

    Modal probes this often, and a healthcheck that depends on a third party
    reports *their* outage as *our* death. The live GHL check is
    ghl_service.test_connection(), which /test and /status use.
    """
    config_ok = True
    missing: list[str] = []
    try:
        for path in ("business.timezone", "crm.calendar_id", "crm.pipeline_id", "crm.stage_id"):
            if not ghl.config_value(path):
                missing.append(path)
                config_ok = False
    except GHLError as exc:  # config file itself unreadable
        return fail(status=500, code="config_error", detail=str(exc), message="Backend mal configurado.")

    body = {
        "service": MODAL_APP_NAME,
        "status": "ok" if config_ok else "degraded",
        "config_ok": config_ok,
        "missing_config": missing,
        "business": ghl.config_value("business.name"),
        "timezone": ghl.config_value("business.timezone"),
    }
    return JSONResponse(status_code=200, content={"ok": True, "data": body, "message": "Backend arriba."})


# --------------------------------------------------------------------------
# Modal — public URL for Retell's custom tools
#
# Modal authenticates via CLI (`modal token new`), never through .env.
# Runtime secrets come from the Modal Secret `agente-voz-credentials`.
#
# Deploy:  modal deploy app/main.py::modal_app
#          (el sufijo ::modal_app es obligatorio — Modal busca por defecto
#           una variable llamada `app` y la nuestra se llama `modal_app`)
# Local:   uvicorn app.main:web_app --reload
# --------------------------------------------------------------------------

try:
    import modal
except ImportError:  # pragma: no cover - local dev without the Modal SDK
    modal = None  # type: ignore[assignment]
    LOG.warning("Modal SDK not installed; serve locally with `uvicorn app.main:web_app --reload`")

if modal is not None:
    image = (
        modal.Image.debian_slim(python_version="3.11")
        .pip_install(
            "fastapi[standard]",
            "requests",
            "pyyaml",
            "pydantic",
            "anthropic",
            "retell-sdk",
            "twilio",
        )
        # sofia.config.yaml must travel with the code: ghl_service resolves it
        # relative to the package root, which is /root inside the image.
        .add_local_file("sofia.config.yaml", "/root/sofia.config.yaml")
        # prompts/ must travel too. Two consumers depend on it: the post-call
        # analysis reads prompts/<industry>.yaml on every call_ended, and the
        # dashboard reads the safety guardrails from it on every prompt save.
        # Without this the image has the code but not the text it operates on —
        # and the failure hides, because the webhook answers 200 by design, so
        # Retell never retries and the raised error only lands in a log.
        .add_local_dir("prompts", "/root/prompts")
        # Clínica Isis MVP demo pages, served at GET /mascota, /checklist, …
        .add_local_dir("demo", "/root/demo")
        .add_local_python_source("app")
    )

    modal_app = modal.App(MODAL_APP_NAME)

    @modal_app.function(
        image=image,
        secrets=[modal.Secret.from_name(MODAL_SECRET_NAME)],
        # The number IS live, so this is no longer optional. Measured 2026-08-06
        # on a real call: the container had gone to sleep, so book_appointment
        # cost 6.5s of cold start on top of its own 5.8s. That is 12.3s of
        # silence against `end_call_after_silence_ms`, and the call died one
        # beat before Sofía could confirm the appointment. The booking existed;
        # the patient hung up never knowing it. That is the exact failure this
        # project is built to avoid.
        min_containers=1,
    )
    @modal.asgi_app()
    def fastapi_app():
        return web_app
