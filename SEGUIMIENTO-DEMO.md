# Seguimiento — Demo Clínica Estética Aurora (voz + WhatsApp)

> Tracker del proyecto. (gbrain MCP desconectado en la sesión → seguimiento en este doc + todos.)
> Última actualización: auditoría multi-agente del backend + análisis de las llamadas reales a Sofía.

## Estado general
- **Voz (Retell + Modal en cuenta dineroconsciente):** agenda, consulta, cancela, reprograma. Voz Selena (colombiana). Verificado en vivo.
- **WhatsApp (whatsapp-saas, Vercel/EasyPanel + Supabase):** agenda/cancela/reprograma, pin de ubicación, prompt v13+. Verificado en vivo (GHL).
- **Calendario:** ambos apuntan a "Calendario DEMO Clínicas" (`VduJfmUeRNfiiTdb6HQW`).
- **Git:** rama `fix/opportunity-...` en fork `Joinsclee/sofia-agente-voz` → PR #1 a `Carlos:main`.

## Mejoras de la demo del WhatsApp/voz — YA APLICADAS (prompt de voz, en vivo)
1. **"No hay espacio" repetido:** en la llamada ofrecía día por día y rechazaba (jueves/viernes/lunes llenos) antes de encontrar hueco. Ahora, ante prisa/"lo más pronto posible", consulta un rango amplio y ofrece el **hueco más cercano directo**.
2. **Sobre-promesa de ubicación:** decía "te llegará la ubicación por WhatsApp" (la llamada no manda un WhatsApp propio de Aurora). Ahora da la dirección de viva voz y **no promete** un mensaje.
3. **Correo sin arroba:** aceptó "cristianfonseca.gmail.com" (sin @). Ahora reconstruye y confirma el arroba; nunca guarda un correo inválido.

## Auditoría del backend (multi-agente) — FIXES DE CÓDIGO aplicados y desplegados
Backend redeployado en Modal (`agente-voz-ghl` + `-worker`, cuenta dineroconsciente). Health `config_ok: true`.
1. **BUG crítico de zona horaria (get/cancel/reprograma):** `find_future_appointment` y `last_appointment_before` devolvían el `start` sin normalizar → Sofía podía **decir la hora corrida** al consultar/cancelar. Ahora normalizan con `.astimezone(_business_timezone())` (+ guarda `None` si no hay contacto/cita).
2. **Contacto fantasma:** `_resolve_caller_contact` hacía `upsert` (escritura) para identificar a quien llama → creaba un contacto vacío si no existía. Ahora usa `ghl_read.find_contact_by_phone` (solo lectura) y devuelve `str | None`.
3. **Repoint silencioso:** si faltaba `TWILIO_PHONE_NUMBER`, el número no se repuntaba **sin avisar** (bug V07/V09). Ahora emite `LOG.warning` explícito.

## MVP para el cierre de Clínica Isis (lunes) — construyendo, en orden: mascota-voz → chat interno → checklist
Decisión del operador: construir los MVPs (contra la recomendación "solo discovery" del análisis — advertencia registrada). Se hace reversible y SIN tocar el demo Aurora en vivo.

### ✅ MVP #1 — "Bianca", MASCOTA VISUAL que gesticula al hablar (COMPLETO, en vivo)
Aclaración del operador: la mascota es un **personaje visual que gesticula mientras habla** (no solo voz). Construido como **avatar web interactivo autocontenido** (no imagen plana — SVG riggeable: boca con lip-sync, parpadeo, gestos, saludo).
- **DEMO EN VIVO (abrir en cualquier dispositivo):** `https://dineroconsciente-digital--agente-voz-ghl-fastapi-app.modal.run/mascota`
  - "Hablar con Bianca" → conversación en vivo: mic → voz neutra de Bianca (Retell web call) → lip-sync por amplitud + gestos. Endpoint `POST /isis-web-call` mintea el token (acotado al agente Isis).
  - "Vista previa" → intro offline (SpeechSynthesis + flap) por si no hay red.
- Archivos: `demo/mascota-bianca.html` (SVG + motor de animación + SDK Retell), endpoints `GET /mascota` y `POST /isis-web-call` en `app/main.py`. Servido mismo-origen (sin CORS). Verificado: render (screenshots idle+talking), 0 errores de consola, 200 en ambos endpoints; Aurora intacto.
- Sigue pendiente: nombre italiano real (placeholder "Bianca"); el personaje es un diseño provisional (cuando la clínica dé su muñequita/estilo, se re-viste).

### ✅ MVP #1 (capa de voz) — agente Retell "Bianca" (COMPLETO y verificado)
- Agente Retell **separado** (`agent_ae9bce89c54c26f046c9950444`, llm `llm_b410de87c56827a9e3db79503136`), no toca Aurora.
- Voz **neutra latinoamericana** `cartesia-Hailey` (requisito #1 del dueño: nada cachaco), expresiva (temp 1.1 + backchannel), velocidad natural 1.0.
- Multi-especialidad (IPS, triage por área + paciente existente con servicio nuevo), identidad "experiencia Isis", guardrails clínicos intactos.
- Nombre **"Bianca" es PLACEHOLDER** — el nombre italiano real (no negociable) lo trae Ariel; se cambia en un solo valor (`MASCOTA_NAME` en `scripts/build_isis_agent.py`).
- Verificado por subagente de gstack; aplicados 8 hallazgos (crítico: quitado "tantito"; embudo de valoración pagada gateado a cirugía/estética; guardrail de resultados generalizado; regionalismos fuera).
- Archivos: `prompts/isis.yaml`, `scripts/build_isis_agent.py`. Prompt en vivo v1.
- **Cómo probar:** Retell → "Isis · Bianca (MVP demo)" → llamada web. Para demo por teléfono el lunes: falta número dedicado (no tocar el de Aurora).
- **Pendiente cliente:** confirmar nombre italiano real; confirmar financiación (Welli/Servicredito) y catálogo/precios reales; Ley 1581 (aviso habeas data) es item de producción, no bloquea el demo.

### ⏳ MVP #2 — chat interno · ⏳ MVP #3 — checklist con trazabilidad
Pendientes. Nota del análisis: el "chat tipo Slack" NO resuelve el dolor real (genera más conversación no trazable); lo que mata el Dolor #1 es un **registro estructurado por paciente/procedimiento con campos obligatorios y firma de turno** — construir el checklist así (módulo reutilizable de la vertical clínicas). whatsapp-saas está EN PRODUCCIÓN en EasyPanel: no desplegar sin cuidado.

## Auditoría del backend — 2º pase (outbound + post-llamada + dashboard) — FIXES aplicados
Segundo agente de auditoría sobre las superficies que el 1º no cubrió. Confirmó OK: auth del panel (token constant-time, ninguna ruta se salta el check), streaming de grabación tras token, publish→repoint, análisis Anthropic (parse robusto), `prompt_history` en `modal.Dict`, imagen de Modal (config+prompts añadidos, sufijo `::modal_app`, worker aparte). Hallazgos corregidos + redeploy + 10 tests de regresión (`tests/test_post_call_resolve.py`):
1. **[HIGH] Contacto equivocado en el análisis post-llamada (outbound / línea de desvío).** `_resolve_contact_id` caía a `from_number` = el número **propio de la clínica** en outbound → escribía el resumen/score en un contacto basura y el paciente real se quedaba sin nada. Ahora usa `metadata.contact_id` (que el worker ya manda) y, si no, la **línea de registro** direccional/anti-desvío (`_line_number`) — el MISMO contacto que escribió el booking.
2. **[HIGH, latente: outbound off] Cooldown del worker roto por el campo DATE.** `record_attempt` escribía ISO-con-offset en un campo **DATE** de GHL → podía rechazarse (perdía el contador → remarcaba cada hora) o volver como epoch-ms sin parsear. Ahora escribe **epoch-ms** y `_parse_last_attempt` tolera epoch-ms/segundos/ISO.
3. **[LOW] Escritura de custom fields todo-o-nada.** Un key renombrado tiraba los 4 campos. Nuevo `strict=False` en `update_contact_fields`: degrada solo el campo faltante (como prometía el comentario).

### PENDIENTE — decisión tuya (NO lo aplico: el panel está congelado a propósito en CLAUDE.md)
- **[MEDIUM] El dashboard solo cuenta el agente inbound.** `_filter_criteria` filtra por `_inbound_agent_id()` → si activas outbound, esas llamadas y sus citas **no aparecen ni suman** en "total". No afecta al demo (outbound off). Si algún día activas outbound y quieres que el panel lo cuente, hay que incluir `_outbound_agent_id()` en el filtro — dime y lo hago.

## Llamadas reales a Sofía — 7 MEJORAS de conversación (prompt v17 inbound / v10 outbound, en vivo)
Basadas en transcribir las llamadas reales (p. ej. call_04: correo dictado "después de la T va una H" que se guardó mal, teléfono repetido cortado, "Perfecto/Listo/Excelente" en cada turno).
1. **Muletillas:** prohibido 3 turnos seguidos con "Perfecto/Listo/Excelente/Entiendo"; el arranque varía y dice algo real, no una muletilla hueca.
2. **Nombre:** 2–3 veces en toda la llamada como máximo (antes lo repetía en cada frase).
3. **Frases de relleno:** rota el mismo sentido con otras palabras (no el mismo libreto cada vez).
4. **Disponibilidad:** nunca pregunta "¿qué día?" antes de mirar la agenda; abre ella con el hueco más cercano.
5. **Correcciones por posición:** "después de la T va una H" → inserta la H en su lugar ("Cristhian", no "Cristianh"); no pega conectores al dominio ("gmail.com", no "gmailpuntocom").
6. **Teléfono:** valida celular colombiano (10 dígitos, empieza en 3) y lo repite agrupado y fluido.
7. **Una pregunta por turno** (incl. síntomas): no interroga en cadena.

## Calendario DEMO — YA AJUSTADO por API (bajo riesgo, aplicado)
- Horarios alineados a **L–V 10:00–17:00** (antes 11:00–19:15 desalineado con el negocio).
- **allowBookingAfter 3h → 1h** (permite huecos cercanos para "lo más pronto posible").
- **appointmentPerSlot 1 → 5** (en demo, reservar no "agota" el cupo → sin "slot ocupado").
- Cancelada la cita huérfana.
- **Quitado el Google Meet** del calendario → reservas presenciales en la dirección de la clínica (sin link de Meet).
- Efecto: el agente ya ofrece el hueco más cercano directo, sin churn.

## PENDIENTE — acciones tuyas en GHL (estructurales; NO las toco porque son config real de JoinsClee)
1. **Disponibilidad limitada por el miembro real del equipo.** El calendario DEMO es round-robin atado al usuario `4n2Lv8VMhkjbq4bnOQjU`, ocupado en otros calendarios reales → solo ~12 cupos (mar/jue). Para disponibilidad ABUNDANTE, **asigna el calendario DEMO a un usuario dedicado** (sin citas reales). El fix del prompt (hueco más cercano) ya enmascara esto, así que no bloquea.
2. **Workflow "Consultoría Funnels & CRM" de JoinsClee.** Ese recordatorio es una automatización real de JoinsClee. Quitar el Meet reduce el ruido, pero si el workflow dispara por cita/tag, **exclúyelo del calendario/pipeline de la demo** (o usa sub-cuenta separada). No modifico workflows reales de JoinsClee.

## Opcionales (calidad)
- Suscripción ElevenLabs (BYOK en Retell) → voz más natural y menos latencia.
- `ANTHROPIC_API_KEY` → resumen/score post-llamada en GHL.
- Confirmación por WhatsApp con marca Aurora (workflow propio) → recién ahí re-activar la promesa de "te llega la ubicación".
