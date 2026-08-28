"""Sicurezza — the governed RAG demo for Clínica Isis.

A chat grounded in the Isis "Base de Verdad" (prompts/sicurezza_kb.md). Nivel 1
(the knowledge) + Nivel 2 (governance): every answer is grounded in approved Isis
knowledge, cites the sections it used, never diagnoses, never invents, and hands
off to a human to close.

Two governance features live here:
  * ROLES ("paciente" | "interno"): the same brain, different permissions. The
    patient never receives the internal knowledge in its context (true isolation,
    not just an instruction) — the internal block of the Base de Verdad is stripped.
  * WEB SEARCH (governed): the model may search the web ONLY for general/educational
    context; Isis-specific facts always come from the Base de Verdad, never the web.

Hard rules honored: runs on an LLM API KEY (never a personal subscription); never
diagnoses; never invents (what is not in the Base de Verdad is escalated).
Provider: OpenAI. The Base de Verdad holds public clinic info — no patient PII.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Literal

import openai
from pydantic import BaseModel, Field, ValidationError

from app.services.ghl_service import _load_env_file

LOG = logging.getLogger(__name__)

MODEL = (os.environ.get("SICUREZZA_MODEL") or "gpt-4o").strip()
MAX_TOKENS = 1600          # room for complete, self-contained answers
MAX_HISTORY = 40           # keep more of the conversation so follow-ups don't lose context

_KB_CANDIDATES = (
    Path(__file__).resolve().parents[2] / "prompts" / "sicurezza_kb.md",
    Path("/root/prompts/sicurezza_kb.md"),
)
_INTERNO_BLOCK = re.compile(r"<!--INTERNO_START-->.*?<!--INTERNO_END-->", re.S)


class SicurezzaError(RuntimeError):
    """The chat could not produce a usable reply."""


class SicurezzaReply(BaseModel):
    respuesta: str = Field(description="Lo que Sicurezza le dice. Cálido, claro, neutro, frases cortas.")
    fuentes: list[str] = Field(default_factory=list)
    estado: Literal["responde", "deriva_humano", "restringido", "urgencia"] = "responde"
    acciones: list[Literal["agendar_whatsapp", "llamar_voz"]] = Field(default_factory=list)


# Structured output for the Responses API (text.format).
_TEXT_FORMAT = {
    "type": "json_schema",
    "name": "sicurezza_reply",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "respuesta": {"type": "string"},
            "fuentes": {"type": "array", "items": {"type": "string"}},
            "estado": {"type": "string", "enum": ["responde", "deriva_humano", "restringido", "urgencia"]},
            "acciones": {"type": "array", "items": {"type": "string", "enum": ["agendar_whatsapp", "llamar_voz"]}},
        },
        "required": ["respuesta", "fuentes", "estado", "acciones"],
    },
}


def _client() -> openai.OpenAI:
    _load_env_file()
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise SicurezzaError("OPENAI_API_KEY is not set")
    return openai.OpenAI(api_key=key)


def _load_kb(rol: str) -> str:
    text = ""
    for path in _KB_CANDIDATES:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            break
    if not text:
        raise SicurezzaError("No se encontró la Base de Verdad (sicurezza_kb.md)")
    if rol == "interno":
        # keep the internal block, drop just the markers
        return text.replace("<!--INTERNO_START-->", "").replace("<!--INTERNO_END-->", "")
    # paciente: the internal block never enters the context (true isolation)
    return _INTERNO_BLOCK.sub("", text)


_WEB_RULE = """
BÚSQUEDA WEB (gobernada): puedes usar la búsqueda web SOLO para contexto general o
educativo (por ejemplo, en qué consiste una técnica o un concepto en general). Los datos
ESPECÍFICOS de Clínica Isis — servicios, precios, protocolos, especialistas, contacto, o si
Isis ofrece algo — SIEMPRE salen de la Base de Verdad, nunca de la web. Nunca uses la web
para diagnosticar. Si complementas con la web, deja claro que es información general, no
específica de Isis.
"""

_PACIENTE_TEMPLATE = """Eres **Sicurezza**, la guía con inteligencia artificial de Clínica Isis,
atendiendo a un **paciente o futuro paciente**.

Respondes con el conocimiento aprobado de Isis de la BASE DE VERDAD de más abajo, con calidez
y cercanía profesionales, en acento neutro, de "tú" con respeto. Reconoces lo que la persona
dijo antes de responder y tienes en cuenta el hilo de la conversación: recuerda lo que ya te
contó y no le pidas repetir.

Da respuestas **completas y cerradas**: incluye el contexto necesario para que la persona no
tenga que volver a preguntar por lo mismo, en lenguaje claro y cálido, SIN tecnicismos. Si el
tema tiene varias partes, estructúralo (una lista corta ayuda), pero sin alargar de más ni
divagar. Cuando aplique, cierra ofreciendo el siguiente paso (agendar la valoración).

Cumples SIEMPRE la Gobernanza de la Base de Verdad: **nunca diagnosticas**, nunca das
indicaciones médicas, nunca cierras precios, nunca prometes resultados; esos temas se defieren
al especialista en la valoración (estado = restringido). Para agendar o cerrar, preparas y
derivas a una persona (estado = deriva_humano). Ante una urgencia médica, indicas buscar
atención inmediata (estado = urgencia). Si algo no está en la Base de Verdad, no lo inventas:
lo dices y derivas a una persona (estado = deriva_humano).
{web}
Devuelves SIEMPRE un objeto con cuatro campos:
- "respuesta": lo que le dices a la persona.
- "fuentes": nombres de las secciones de la Base de Verdad que usaste (p. ej. "Servicios",
  "Valoración y agendamiento", "Educación al paciente", "Isis Gold", "Contacto"). Vacía si derivas por no tener el dato.
- "estado": "responde" | "restringido" | "deriva_humano" | "urgencia".
- "acciones": botones útiles AHORA. Solo "agendar_whatsapp" (agendar por WhatsApp) y
  "llamar_voz" (agendar hablando con la asistente de voz). Cuando propongas agendar o derives a
  una persona, incluye normalmente ["agendar_whatsapp","llamar_voz"]. Si solo pide información
  general y no hay acción útil, deja "acciones" vacía ([]).

=========================  BASE DE VERDAD ISIS  =========================
{kb}
========================================================================="""

_INTERNO_TEMPLATE = """Eres **Sicurezza en modo interno**, asistiendo al **equipo de Clínica Isis**
(recepción, líderes, especialistas, gerencia). No estás hablando con un paciente.

Respondes de forma **directa, completa y operativa**, como una colega del equipo. Das el
contexto suficiente para que la consulta quede resuelta en una sola respuesta, con lenguaje
claro y sin tecnicismos innecesarios. Tienes en cuenta el hilo de la conversación (recuerda lo
ya dicho, no pidas repetir). Puedes consultar y compartir la información de la BASE DE VERDAD,
**incluida la sección de USO INTERNO** (especialidades, requisitos por procedimiento, procesos
operativos). No uses tono de venta.

Reglas: lo que NO esté en la Base de Verdad (protocolos clínicos completos, nombres/agendas
reales, datos de un paciente concreto) se carga con la clínica en producción: dilo con
transparencia, **no lo inventes** (estado = deriva_humano). Aun en modo interno, **no
reemplazas el criterio médico** del especialista para un caso clínico.
{web}
Devuelves SIEMPRE un objeto con cuatro campos:
- "respuesta": la respuesta al equipo, clara y operativa.
- "fuentes": nombres de las secciones de la Base de Verdad que usaste (p. ej. "Uso interno",
  "Servicios", "Procedimientos"). Vacía si no aplica.
- "estado": normalmente "responde"; "deriva_humano" si el dato aún no está cargado.
- "acciones": SIEMPRE vacía ([]) en modo interno (los botones de agendar son para pacientes).

=========================  BASE DE VERDAD ISIS  =========================
{kb}
========================================================================="""


def _system_prompt(rol: str) -> str:
    tpl = _INTERNO_TEMPLATE if rol == "interno" else _PACIENTE_TEMPLATE
    return tpl.format(kb=_load_kb(rol), web=_WEB_RULE)


def _clean_history(messages: list[dict]) -> list[dict]:
    out: list[dict] = []
    for m in messages or []:
        role = (m or {}).get("role")
        content = ((m or {}).get("content") or "").strip()
        if role in ("user", "assistant") and content:
            out.append({"role": role, "content": content[:4000]})
    if len(out) > MAX_HISTORY:
        out = out[-MAX_HISTORY:]
    while out and out[-1]["role"] != "user":
        out.pop()
    return out


def _fallback(rol: str) -> SicurezzaReply:
    if rol == "interno":
        return SicurezzaReply(
            respuesta="Ahora mismo no puedo consultarlo. Inténtalo de nuevo en un momento.",
            fuentes=[], estado="deriva_humano", acciones=[],
        )
    return SicurezzaReply(
        respuesta=("Ahora mismo no puedo confirmarte eso con seguridad. Déjame pasarte con una "
                   "persona del equipo de Isis para que te ayude directamente."),
        fuentes=[], estado="deriva_humano", acciones=["agendar_whatsapp", "llamar_voz"],
    )


def chat(messages: list[dict], rol: str = "paciente") -> tuple[SicurezzaReply, bool]:
    """Return (reply, web_used). Never raises except on an empty conversation."""
    rol = "interno" if rol == "interno" else "paciente"
    history = _clean_history(messages)
    if not history:
        raise SicurezzaError("empty conversation")

    try:
        response = _client().responses.create(
            model=MODEL,
            instructions=_system_prompt(rol),
            input=history,
            tools=[{"type": "web_search"}],
            text={"format": _TEXT_FORMAT},
            max_output_tokens=MAX_TOKENS,
        )
    except SicurezzaError as exc:
        LOG.error("Sicurezza config error: %s", exc)
        return _fallback(rol), False
    except openai.OpenAIError as exc:
        LOG.error("Sicurezza OpenAI error: %s", exc)
        return _fallback(rol), False

    web_used = any(getattr(it, "type", "") == "web_search_call" for it in (response.output or []))
    try:
        data = json.loads(response.output_text or "{}")
        return SicurezzaReply(**data), web_used
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        LOG.error("Sicurezza parse error: %s", exc)
        return _fallback(rol), False
