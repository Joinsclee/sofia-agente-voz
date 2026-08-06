#!/usr/bin/env python3
"""Discover the GoHighLevel ids Sofía needs, straight from the Location.

GHL is referenced, never created: the calendar, the pipeline and the custom
fields must already exist in the subaccount, and their ids go into
`sofia.config.yaml` under `crm:`. Hunting them through the GHL UI is slow and
easy to get wrong, so this reads them from the API instead.

Read-only by default. It prints:

  1. Calendarios    — id, nombre y si está activo.
  2. Pipelines      — id, nombre y todas sus etapas con id.
  3. Custom fields  — cuáles de los 7 que Sofía necesita existen y cuáles no.
  4. Un bloque YAML listo para pegar en `sofia.config.yaml`.

    python scripts/descubrir_ghl.py

`--crear-campos` is the one writing mode: it creates the missing custom fields
in the Location. It asks for confirmation first, because it writes to a real
CRM. Without those fields GHL drops the post-call summary silently.

    python scripts/descubrir_ghl.py --crear-campos

Needs HIGHLEVEL_PIT and HIGHLEVEL_LOCATION_ID in .env. Nothing else.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import httpx  # noqa: E402

from app.services.ghl_service import _load_env_file  # noqa: E402

_API_BASE = "https://services.leadconnectorhq.com"
_API_VERSION = "2021-07-28"

# The 7 fields Sofía writes. `key` is the config key under crm.custom_fields
# (or outbound.tracking_fields); `name` is what GHL must be told to name the
# field so the generated fieldKey comes out as `contact.<name>` exactly.
_CAMPOS_SOFIA: list[tuple[str, str, str, str]] = [
    # (config path, GHL field name, dataType, para qué sirve)
    ("crm.custom_fields.reason_for_visit", "reason_for_visit", "TEXT", "motivo de la llamada"),
    ("crm.custom_fields.interes_score", "interes_score", "NUMERICAL", "qué tan interesado, 1-10"),
    ("crm.custom_fields.nivel_urgencia", "nivel_urgencia", "TEXT", "urgente | normal | baja"),
    ("crm.custom_fields.probabilidad_asistir", "probabilidad_asistir", "NUMERICAL", "probabilidad de asistir, 1-10"),
    ("crm.custom_fields.resumen_llamada", "resumen_llamada", "LARGE_TEXT", "resumen post-llamada"),
    ("outbound.tracking_fields.last_attempt", "ultimo_intento_outbound", "DATE", "cuándo se marcó por última vez"),
    ("outbound.tracking_fields.attempts", "intentos_outbound", "NUMERICAL", "cuántas veces se ha intentado"),
]

_OK = "✓"
_BAD = "✗"


def _section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def _creds() -> tuple[str, str]:
    """Read the two values this script needs, or explain what is missing."""
    _load_env_file()
    pit = (os.environ.get("HIGHLEVEL_PIT") or "").strip()
    location = (os.environ.get("HIGHLEVEL_LOCATION_ID") or "").strip()

    faltan = []
    if not pit:
        faltan.append("HIGHLEVEL_PIT (GHL → Settings → Private Integrations)")
    if not location:
        faltan.append("HIGHLEVEL_LOCATION_ID (GHL → Settings → Business Profile)")
    if faltan:
        print("\nMe faltan credenciales en .env para poder preguntarle a GHL:\n")
        for f in faltan:
            print(f"  {_BAD} {f}")
        print("\nPégalas en .env y vuelve a correr esto.\n")
        raise SystemExit(1)
    return pit, location


def _get(client: httpx.Client, path: str, **params: Any) -> dict[str, Any]:
    response = client.get(f"{_API_BASE}{path}", params=params or None)
    if response.status_code == 401:
        raise SystemExit(
            "\nGHL respondió 401. El PIT no es válido para esta Location, o le faltan\n"
            "los scopes contacts, calendars y opportunities. Revísalo en\n"
            "GHL → Settings → Private Integrations.\n"
        )
    response.raise_for_status()
    return response.json()


def _calendarios(client: httpx.Client, location: str) -> list[dict[str, Any]]:
    data = _get(client, "/calendars/", locationId=location)
    return data.get("calendars", [])


def _pipelines(client: httpx.Client, location: str) -> list[dict[str, Any]]:
    data = _get(client, "/opportunities/pipelines", locationId=location)
    return data.get("pipelines", [])


def _custom_fields(client: httpx.Client, location: str) -> list[dict[str, Any]]:
    data = _get(client, f"/locations/{location}/customFields")
    return data.get("customFields", [])


def _crear_campo(client: httpx.Client, location: str, name: str, data_type: str) -> dict[str, Any]:
    response = client.post(
        f"{_API_BASE}/locations/{location}/customFields",
        json={"name": name, "dataType": data_type, "model": "contact"},
    )
    response.raise_for_status()
    return response.json()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Descubre los ids de GoHighLevel que Sofía necesita.")
    parser.add_argument(
        "--crear-campos",
        action="store_true",
        help="Crea en la Location los custom fields que falten. Escribe en el CRM: pide confirmación.",
    )
    args = parser.parse_args(argv)

    pit, location = _creds()
    headers = {
        "Authorization": f"Bearer {pit}",
        "Version": _API_VERSION,
        "Accept": "application/json",
    }

    with httpx.Client(headers=headers, timeout=30.0) as client:
        print(f"\nLocation: {location}")

        _section("Calendarios")
        calendarios = _calendarios(client, location)
        if not calendarios:
            print("  (ninguno) — Sofía no puede agendar sin un calendario en esta Location.")
        for cal in calendarios:
            activo = cal.get("isActive")
            marca = _OK if activo else _BAD
            estado = "activo" if activo else "INACTIVO — no devuelve horarios"
            print(f"  {marca} {cal.get('id'):<24} {cal.get('name')}  ({estado})")

        _section("Pipelines y sus etapas")
        pipelines = _pipelines(client, location)
        for pipe in pipelines:
            print(f"\n  {pipe.get('id'):<24} {pipe.get('name')}")
            for stage in pipe.get("stages", []):
                print(f"      {stage.get('id'):<40} {stage.get('name')}")

        _section("Custom fields que Sofía necesita")
        existentes = {f.get("fieldKey"): f for f in _custom_fields(client, location)}
        faltantes: list[tuple[str, str, str, str]] = []
        for config_path, name, data_type, para_que in _CAMPOS_SOFIA:
            field_key = f"contact.{name}"
            encontrado = existentes.get(field_key)
            if encontrado:
                print(f"  {_OK} {field_key:<38} ya existe ({encontrado.get('dataType')})")
            else:
                print(f"  {_BAD} {field_key:<38} FALTA · {data_type} · {para_que}")
                faltantes.append((config_path, name, data_type, para_que))

        if faltantes and not args.crear_campos:
            print(
                f"\n  Faltan {len(faltantes)}. Esto NO degrada en silencio: revienta.\n"
                "  ghl_service._custom_fields_payload lanza GHLError y el endpoint\n"
                "  devuelve 502. Como `reason` es un parámetro declarado de las tools de\n"
                "  Retell, es el payload normal de Sofía: sin estos campos, una llamada\n"
                "  real NO agenda nada.\n"
                "  Créalos con:  python scripts/descubrir_ghl.py --crear-campos"
            )

        if faltantes and args.crear_campos:
            _section("Crear los campos que faltan")
            print(f"  Voy a crear {len(faltantes)} custom fields en la Location {location}.")
            print("  Esto ESCRIBE en el CRM. Escribe `si` para confirmar: ", end="")
            if input().strip().lower() not in {"si", "sí", "s"}:
                print("  Cancelado. No toqué nada.")
                return 0
            for _config_path, name, data_type, _para_que in faltantes:
                creado = _crear_campo(client, location, name, data_type)
                campo = creado.get("customField", creado)
                print(f"  {_OK} creado {campo.get('fieldKey', 'contact.' + name):<38} id {campo.get('id')}")

        _section("Para pegar en sofia.config.yaml")
        activos = [c for c in calendarios if c.get("isActive")]
        sugerido = activos[0] if activos else (calendarios[0] if calendarios else None)
        if sugerido:
            print(f'  calendar_id: "{sugerido.get("id")}"        # {sugerido.get("name")}')
        else:
            print('  calendar_id: "PENDIENTE_CALENDAR_ID"   # no hay calendarios en esta Location')
        print("\n  Elige el pipeline de arriba cuya etapa de 'no asistió' vaya a alimentar el")
        print("  outbound, y copia su id y los de sus etapas al bloque crm: del YAML.")
        print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
