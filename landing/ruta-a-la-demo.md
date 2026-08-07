# Ruta a la demo — qué falta y en qué orden

Objetivo: poder mostrar el sistema completo funcionando (voz **y** WhatsApp) lo
antes posible, sin prometer nada que no se pueda entregar.

Estado a 2026-08-06.

---

## Lo que YA se puede demostrar hoy

El agente de **voz** está operativo de punta a punta y probado con llamadas
reales:

```
☎️  +1 424 678 1756
```

Contesta, detecta urgencia, califica, consulta el calendario real, agenda, crea
el contacto, abre la oportunidad, pone tags de temperatura y cuelga sola.
Verificado contra GoHighLevel con una llamada completa.

**Esto ya basta para una demo comercial.** El bloque 2 del VSL (la demo de voz)
se puede grabar hoy.

Sin `ANTHROPIC_API_KEY` se pierde solo el resumen automático post-llamada. Es lo
más vistoso de mostrar en la ficha del paciente, y cuesta cinco minutos
añadirlo.

---

## Lo que falta para WhatsApp, en orden de dependencia

### Bloque A — trámites de terceros. Arrancan HOY, no dependen de código

Cada día que no arrancan se le resta a los 30 que promete la oferta.

| Trámite | Para qué | Quién lo hace |
| --- | --- | --- |
| **WhatsApp Business API con Meta** | Sin WABA verificada no hay canal, punto | La clínica es la dueña del número; JoinsClee acompaña |
| **Verificación de identidad de Retell** | Desbloquea llamadas SALIENTES. Sin esto no hay "rescate de sillas vacías" | JoinsClee |
| **Bundle regulatorio Twilio Colombia** | Solo si el agente de voz debe contestar en un número colombiano | JoinsClee, con documentación corporativa |

> Ojo con Retell: **no hace llamadas salientes a Colombia** (México sí). El
> rescate de no-shows, que la oferta llama "la pieza que paga el sistema", no
> tiene ruta técnica confirmada al país. Abrir ticket con soporte de Retell hoy
> y, si no hay ruta, **retirarlo de la oferta antes de venderlo, no después**.

### Bloque B — lo que ya está arreglado en el código

Hecho en esta pasada, rama `fix/openrouter-allowlist` de `whatsapp-saas`:

- Candado de OpenRouter: allowlist + política de proveedor en los 6 clientes.
  Ningún dato de paciente sale a un modelo no aprobado.
- Normalización E.164: rechaza en vez de adivinar la lada. Sin esto, voz y
  WhatsApp guardaban al mismo paciente como dos personas distintas.
- El sync con GoHighLevel **ahora funciona**. Llevaba desde junio fallando en
  silencio por un índice único que no existía.
- WhatsApp ya no borra los tags de temperatura que pone la voz, y marca
  `source` para que el CRM distinga los dos canales.

### Bloque C — lo que falta arreglar antes del piloto

En orden, cada uno desbloquea al siguiente:

1. **Portar `ensure_opportunity_stage`.** Sin esto, todo paciente que primero
   llamó y luego escribe queda invisible en el funnel.
2. **Rechazar el ISO en UTC** en `schedule-highlevel.ts` y forzar
   `America/Bogota`. Hoy un modelo que devuelva `...Z` agenda cinco horas
   corrida en el mismo calendario donde la voz agenda bien.
3. **Preservar los guardrails clínicos** en el modo degradado del
   `cost-enforcer`. Al agotarse el presupuesto recorta el prompt a 20 líneas y
   los guardrails van al final: el agente de una IPS pierde sus restricciones
   médicas y sigue conversando.
4. **`first_response_at` y métricas por canal.** Es el único instrumento capaz
   de medir la garantía de 60 segundos. Sin esto la garantía es incobrable e
   indefendible en ambos sentidos.

### Bloque D — decisiones de negocio que bloquean, no son código

- **Los cinco números.** El esquema actual admite **uno por workspace**
  (`UNIQUE(workspace_id, provider)`). O la clínica consolida en un solo WABA, o
  hay que construir el modelo de datos. Es la promesa central de la oferta y
  cambia el precio y el plazo.
- **La garantía de <60s, reescrita y medible.** Desde qué instante corre el
  reloj (recomendación: desde el último mensaje del paciente), qué cuenta como
  respondida, qué queda excluido. El sistema espera 30 segundos de silencio por
  diseño antes de responder: sin bajar ese parámetro, la garantía se incumple
  sola.
- **Ley 1581 con abogado.** El consentimiento no existe en ninguno de los dos
  sistemas y se está vendiendo como componente central.

---

## La ruta más rápida a una demo completa

**Opción rápida (días): demo de voz real + WhatsApp guionizado.**
La voz se demuestra en vivo llamando al número. WhatsApp se muestra con la
conversación del documento, presentada como lo que es: el guion del sistema, no
una captura de producción. Honesto y suficiente para vender el diagnóstico.

**Opción completa (semanas): las dos en vivo.**
Exige el bloque A cerrado (WABA aprobada), el bloque C terminado, y un piloto
medido de dos semanas sobre un solo número.

> **Regla de corte:** no se firma sin el volumen real de llamadas y mensajes de
> la clínica, y no se presenta el rescate de no-shows sin ruta confirmada a
> Colombia.

---

## Sobre la landing

Construida en `Downloads/Paginas Web/JoinsClee-Clinicas/index.html`, con el copy
del documento y el branding real de la marca (coral `#CC785C` sobre crema,
Fraunces + Inter + JetBrains Mono).

Lleva cuatro marcadores de pendiente visibles en la propia página:

1. El VSL de 8 minutos, por grabar.
2. El bloque del número de prueba. **No publicarlo todavía**: el documento pide
   30+ llamadas internas de afinado, topes de presupuesto y límite de duración
   por llamada. Y el WhatsApp de prueba aún no existe.
3. El bloque de credibilidad de "quiénes somos", que el propio documento marca
   como la sección más débil. Mientras no haya un caso verificable, omitir antes
   que inflar.
4. El botón del diagnóstico, por conectar al calendario de GoHighLevel.
