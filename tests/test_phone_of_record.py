"""Which number gets written to the CRM — the line, or what the patient said.

Every test here exists because of a real defect, not a hypothetical one.

The original one was the phantom number: Sofía confirmed a caller's digits
correctly out loud, then handed book_appointment a re-transcribed, corrupted
version. The appointment booked against a line that does not exist, the patient
hung up happy, and the clinic could never reach them. The fix was to trust the
telephony envelope over speech-to-text.

The second one is the reason this file exists. Clinics do not want to change
the number printed on their ads, so they forward it instead of porting it. Some
carriers preserve the caller's ID across that hop; some replace it with the
forwarding line. If it is replaced, `from_number` is the clinic's own number,
the line still outranks the spoken digits, and every patient is written against
it. `upsert_contact` is idempotent by phone, so they do not pile up as
duplicates — they collapse into ONE contact holding every appointment, and the
clinic can call none of them back. Nothing raises. The better the agent works,
the more damage it does.
"""

from __future__ import annotations

import pytest

from app.main import RetellToolRequest, phone_of_record

CLINICA = "+576044442321"  # la línea publicada de la clínica
PACIENTE = "+573182354883"


def req(**kw) -> RetellToolRequest:
    return RetellToolRequest(call_id="call_test", **kw)


# --------------------------------------------------------------------------
# El caso original: la línea gana sobre lo que el modelo transcribió
# --------------------------------------------------------------------------


def test_la_linea_gana_sobre_los_digitos_hablados():
    r = req(direction="inbound", from_number=PACIENTE, to_number=CLINICA)
    # el modelo corrompió un dígito al re-transcribir su propia confirmación
    phone, source = phone_of_record(r, "+573182354888")
    assert phone == PACIENTE
    assert source == "line"


def test_sin_pata_pstn_se_usa_lo_hablado():
    """Llamada web de prueba: no hay línea, el número dictado es todo lo que hay."""
    phone, source = phone_of_record(req(), PACIENTE)
    assert phone == PACIENTE
    assert source == "spoken"


def test_outbound_usa_el_numero_que_marcamos():
    r = req(direction="outbound", from_number=CLINICA, to_number=PACIENTE)
    phone, source = phone_of_record(r, None)
    assert phone == PACIENTE
    assert source == "line"


# --------------------------------------------------------------------------
# El desvío: el caller ID reescrito no identifica a nadie
# --------------------------------------------------------------------------


def test_desvio_que_reescribe_el_caller_id_no_secuestra_al_paciente():
    """from == to significa que el operador reescribió el caller ID al desviar."""
    r = req(direction="inbound", from_number=CLINICA, to_number=CLINICA)
    phone, source = phone_of_record(r, PACIENTE)
    assert phone == PACIENTE, "debe caer al número dictado, no a la línea de la clínica"
    assert source == "spoken"


def test_el_desvio_se_detecta_aunque_el_formato_difiera():
    """El operador puede entregar el mismo número sin '+' o con separadores."""
    r = req(direction="inbound", from_number="6044442321", to_number=CLINICA)
    phone, source = phone_of_record(r, PACIENTE)
    assert phone == PACIENTE
    assert source == "spoken"


def test_desvio_sin_numero_dictado_falla_honestamente():
    """Sin línea utilizable y sin número dictado, no se inventa: se levanta."""
    r = req(direction="inbound", from_number=CLINICA, to_number=CLINICA)
    with pytest.raises(ValueError):
        phone_of_record(r, None)


def test_un_paciente_que_llama_desde_otra_linea_sigue_ganando():
    """El guard no debe dispararse cuando el caller ID sí es del paciente."""
    r = req(direction="inbound", from_number=PACIENTE, to_number=CLINICA)
    phone, source = phone_of_record(r, "+570000000000")
    assert phone == PACIENTE
    assert source == "line"
