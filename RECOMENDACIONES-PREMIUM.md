# Qué contratar para dejar la demo de Clínica Isis "super premium"

> Investigación 2026 (voz + mascota + UI). Base actual: Retell + FastAPI/Modal + demos web autocontenidos.

## A) VOZ — la más natural, español neutro LatAm

**Dato clave:** Retell **no** soporta "pegar tu API key de ElevenLabs" (no hay BYOK). PERO ElevenLabs **es proveedor nativo dentro de Retell** — se elige en el agente, sin key y **$0 de suscripción**; solo pagas por minuto.

Estructura de costo en Retell: infra de voz $0.055/min + recargo según proveedor:
- **Cartesia (Sonic): +$0.015/min** → ~$0.07/min de voz (la actual de Bianca ya es Cartesia Hailey, neutra LatAm).
- **ElevenLabs: +$0.040/min** → ~$0.095/min de voz. Usar **Flash v2.5** (≈75 ms, ideal para tiempo real; v3 NO sirve para realtime).
- Todo incluido: **web ~$0.13/min (ElevenLabs) / ~$0.10/min (Cartesia)**. En teléfono colombiano sumar Twilio: entrante local +$0.0905/min, toll-free +$0.1752/min. **La demo web no tiene costo de telefonía.**

**Recomendación:**
1. **ElevenLabs Flash v2.5** con una voz neutra LatAm de la biblioteca → en Retell: agente → Voz → "Add custom voice" → buscar biblioteca. Techo de calidad. Rechazar voces de acento colombiano/cachaco.
2. **Cartesia Sonic** (lo que ya usa Bianca) — 90% de la calidad a ¼ del recargo. Excelente valor.

> **Contratar:** NADA de suscripción para la voz. Solo el costo por minuto. Opcional: ElevenLabs **Creator $22/mo** SOLO si quieres una voz clonada exclusiva publicada (no hace falta para la demo). **Se puede implementar ya** (cambio reversible en el agente).

## B) MASCOTA — muñequita de marca que gesticula al hablar

La clínica quiere un **personaje dibujado de marca** (no una persona foto-real). Eso descarta HeyGen/Tavus/D-ID/Simli/Synthesia (son **caras humanas**, sirven solo como referencia de realismo).

**Recomendación:**
1. **Mascot.bot** ($49/mo, ~$0.04/min) — hecho exactamente para esto: lip-sync en tiempo real de **mascotas 2D de marca** (personajes Rive) en el navegador, <10 ms, y **acepta audio externo** (puede usar la voz de Bianca de Retell). Integración ~10 min. + una **ilustración/rig de Bianca por encargo (Rive/Live2D): $500–$1.200 una sola vez.**
2. **Alternativa más barata:** encargar solo el rig Live2D/Rive ($300–$800 una vez) y animarlo con **el lip-sync por Web Audio que ya construimos** — casi sin costo recurrente, 100% propio.
3. **"Lo más realista sin importar costo":** Tavus CVI (Phoenix-4, ~$59/mo + $0.37/min) — pero es una **cara humana**, no una muñequita de marca. Solo como referencia de realismo.

> **Contratar:** **Mascot.bot $49/mo + ~$800 una vez** por el personaje ilustrado/riggeado de Bianca. Total para una mascota de marca premium que habla: **~$49/mo + ~$800 único.**

## C) UI de los tableros (checklist + chat) — referencias premium

Nada que licenciar. Construir con **shadcn/ui + Radix + Tailwind + tokens Geist** (todo gratis) y copiar el "feel" de:
1. **Linear** — norte del "premium calmado": densidad, tipografía contenida, motion interrumpible.
2. **Vercel / Geist** — casi monocromo; el color solo comunica estado.
3. **Attio** — IA como superficie de primera clase + tablas de datos.
4. **Intercom / consola Twilio** — patrón de chat/inbox premium.
5. **Notion Calendar** — calma tipográfica para vistas de agenda/checklist.

> **Contratar:** $0. Es tiempo de diseño, no licencias. (Ya estoy aplicando este craft a los tres demos.)

## Resumen para decidir
- **Voz:** ElevenLabs Flash v2.5 en Retell (~$0.13/min web, $0 suscripción). Implementable ya.
- **Mascota:** Mascot.bot $49/mo + ~$800 por el rig de Bianca. (O solo el rig + nuestro lip-sync actual.)
- **UI:** shadcn/Radix/Tailwind/Geist, estilo Linear × Vercel × Intercom. $0 licencias.

_Flags: precio por minuto del LLM Haiku en Retell y PlayHT-en-Retell no verificados al dólar; suscripción de avatar custom de HeyGen (~$475/mo) "a última revisión"; tarifas Twilio Colombia son de lista. Revalidar antes de cotizar al cliente._
