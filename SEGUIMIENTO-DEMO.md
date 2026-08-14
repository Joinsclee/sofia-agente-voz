# Seguimiento — Demo Clínica Estética Aurora (voz + WhatsApp)

> Tracker del proyecto. (gbrain MCP desconectado en la sesión → seguimiento en este doc + todos.)
> Última actualización a partir del análisis de `Agendamiento de citas con IA en WhatsApp.mp4`.

## Estado general
- **Voz (Retell + Modal en cuenta dineroconsciente):** agenda, consulta, cancela, reprograma. Voz Selena (colombiana). Verificado en vivo.
- **WhatsApp (whatsapp-saas, Vercel/EasyPanel + Supabase):** agenda/cancela/reprograma, pin de ubicación, prompt v13+. Verificado en vivo (GHL).
- **Calendario:** ambos apuntan a "Calendario DEMO Clínicas" (`VduJfmUeRNfiiTdb6HQW`).
- **Git:** rama `fix/opportunity-...` en fork `Joinsclee/sofia-agente-voz` → PR #1 a `Carlos:main`.

## Mejoras de la demo del WhatsApp/voz — YA APLICADAS (prompt de voz, en vivo)
1. **"No hay espacio" repetido:** en la llamada ofrecía día por día y rechazaba (jueves/viernes/lunes llenos) antes de encontrar hueco. Ahora, ante prisa/"lo más pronto posible", consulta un rango amplio y ofrece el **hueco más cercano directo**.
2. **Sobre-promesa de ubicación:** decía "te llegará la ubicación por WhatsApp" (la llamada no manda un WhatsApp propio de Aurora). Ahora da la dirección de viva voz y **no promete** un mensaje.
3. **Correo sin arroba:** aceptó "cristianfonseca.gmail.com" (sin @). Ahora reconstruye y confirma el arroba; nunca guarda un correo inválido.

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
