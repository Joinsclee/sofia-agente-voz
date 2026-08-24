"""Build the Clínica Isis 'mascota con voz' demo agent — SEPARATE from Aurora.

Renders prompts/isis.yaml with an Isis-specific context (never reads
sofia.config.yaml, so the live Aurora demo is untouched), creates a fresh
Retell LLM + agent with a NEUTRAL Latin-American voice, publishes it, and points
its tools at the existing Modal backend so booking works in the demo.

Idempotency: this CREATES new Retell objects each run. Run once; capture the
ids it prints. To update the prompt later, use update on the printed llm_id.
"""
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path("/Users/cristhian/Sofia-agente-de-voz")
load_dotenv(ROOT / ".env")
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from app.services import retell_service as rs  # noqa: E402

BACKEND = "https://dineroconsciente-digital--agente-voz-ghl-fastapi-app.modal.run"
# ElevenLabs "Amaf - Melodic Colombian" imported into the Retell workspace via
# voice.add_resource (provider_voice_id 4kaLaTbziI05Jwh8zWad). Warm, mature
# Colombian female voice — chosen by the client team over a Mexican-sounding
# LatAm voice (the earlier "Lucy" read as Mexican). Neutral Colombian accent.
# Fallback if the custom voice is ever missing: "cartesia-Hailey-Spanish-latin-america".
NEUTRAL_VOICE = "custom_voice_6dd7d2a3df0c4327560c133af5"
MASCOTA_NAME = "Bianca"  # PLACEHOLDER — gerente's real non-negotiable Italian name pending (Ariel)

# --- Isis multi-specialty catalog, spoken-friendly, prices APPROX (demo values) ---
TREATMENTS = """  Cirugía plástica:
  - Rinoplastia: desde $8.000.000
  - Mamoplastia de aumento: desde $10.000.000
  - Lipoescultura / lipoabdominoplastia: desde $9.000.000
  - Blefaroplastia (párpados): desde $4.000.000
  Medicina estética:
  - Toxina botulínica (bótox): desde $600.000
  - Ácido hialurónico (rellenos): desde $900.000
  - Depilación láser: desde $80.000 por sesión
  - Limpieza facial profunda: desde $150.000
  Otras especialidades (odontología, ginecología, otorrinolaringología, medicina interna):
  - El precio se define en la valoración con el especialista del área"""

ISIS_CTX = {
    "business.name": "Clínica Isis",
    "business.address": "Envigado, Antioquia",
    "business.hours": "Lunes a viernes de 7:00 a 19:00, sábados en la mañana",
    "business.website": "clinicaisis.com",
    "business.timezone": "America/Bogota",
    "business.treatments": TREATMENTS,
    "business.consultation_price": (
        "$80.000 COP · REEMBOLSABLE: se descuenta del procedimiento si la persona lo realiza"
    ),
    "agent.name": MASCOTA_NAME,
    "agent.personality": (
        "cálida, cercana, atenta y con una sonrisa que se oye; consiente al "
        "paciente sin empalagar (la 'experiencia Isis')"
    ),
}

def main():
    data = yaml.safe_load((ROOT / "prompts" / "isis.yaml").read_text(encoding="utf-8"))
    inbound = rs.render_prompt(data["inbound_prompt"], context=ISIS_CTX)
    print(f"Rendered inbound prompt: {len(inbound)} chars")

    tools = rs.build_custom_functions(BACKEND)
    begin = f"Clínica Isis, te atiende {MASCOTA_NAME}, ¿en qué te puedo ayudar?"

    c = rs._client()
    llm = c.llm.create(
        model=rs.MODEL_HAIKU,
        model_temperature=0.35,
        general_prompt=inbound,
        general_tools=tools,
        begin_message=begin,
        start_speaker="agent",
    )
    print(f"LLM created: {llm.llm_id}")

    agent = c.agent.create(
        response_engine={"type": "retell-llm", "llm_id": llm.llm_id},
        agent_name=f"Isis · {MASCOTA_NAME} (MVP demo)",
        voice_id=NEUTRAL_VOICE,
        language=rs.LANGUAGE_LATAM_SPANISH,
        voice_speed=1.0,          # natural, not slow (fixes earlier "habla despacio")
        voice_temperature=1.1,    # expressive -> less robotic (client's #1 ask)
        enable_backchannel=True,
        backchannel_frequency=0.6,
        interruption_sensitivity=1.0,
        responsiveness=1.0,
        webhook_url=f"{BACKEND}/retell-webhook",
        end_call_after_silence_ms=rs.DEFAULT_END_CALL_AFTER_SILENCE_MS,
        max_call_duration_ms=rs.DEFAULT_MAX_CALL_DURATION_MS,
    )
    print(f"Agent created: {agent.agent_id}")

    # A fresh agent has no published version -> the panel/testing needs one.
    pub = rs.publish_initial_version(agent.agent_id)
    print(f"Published: {pub}")

    print("\n=== ISIS MVP AGENT READY ===")
    print(f"agent_id : {agent.agent_id}")
    print(f"llm_id   : {llm.llm_id}")
    print(f"voice    : {NEUTRAL_VOICE} (neutral LatAm)")
    print(f"name     : {MASCOTA_NAME} (placeholder)")
    print(f"backend  : {BACKEND}")

if __name__ == "__main__":
    main()
