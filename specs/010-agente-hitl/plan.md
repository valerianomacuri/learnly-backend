# Implementation Plan: 10 · Agente: flujo de reembolso con HITL

**Feature**: `010-agente-hitl` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/10-agente-hitl.md`

Spec: [spec.md](spec.md) · Tareas: [tasks.md](tasks.md) · Constitution: [`.specify/memory/constitution.md`](../../.specify/memory/constitution.md)

## Cambios

### Estado y nodos
- Activar `awaiting` en `entry_router` (turno siguiente entra directo a `collect_refund_info`).
- `collect_refund_info` (LLM + tools de lectura 2, 3): resuelve la compra (pregunta si hay varias candidatas, verifica propiedad) y extrae `reason_category/reason_text`. **No avanza sin razón válida (BR-12)**; si falta algo → fija `awaiting` y termina el turno con la pregunta.
- `check_eligibility` (determinista): tool 4; ramifica elegible / no. Resultado guardado en `state.eligibility`.
- `explain_ineligible` (LLM + RAG): narra los `reason_codes` con **citas deterministas** del mapa (`reason_map.citations_for`) + búsqueda semántica complementaria; ofrece alternativas válidas; no crea nada.
- `request_confirmation` (**HITL**): **`interrupt()` es lo primero que ejecuta el nodo** (el nodo se reejecuta al reanudar; nada con efectos antes). Payload `{kind:"refund_confirmation", interrupt_id, purchase, amount, currency, refund_method, reason, window_deadline, policy_citations, hash, expires_at}` construido por la tool 5 `prepare_refund_confirmation` (determinista, valida BR-12, el token firmado **no** entra al contexto del LLM). Valor de reanudación validado: `{interrupt_id, decision: confirm|cancel|edit, edited_reason?}`; `edit` → `collect_refund_info`; `cancel` → respuesta de cierre sin escribir.
- `create_refund` (determinista): tool 6 `create_refund_request` con argumentos **tomados del estado** (nunca del LLM), `confirmation_proof` y `Idempotency-Key = sha256(thread_id + hash)`; llama a `RefundService.create_request(channel=CHAT, chat_thread_id=…)` con `Principal(acting_via="agent")`; maneja 422 (vuelve a `explain_ineligible`) y 409 (idempotente → éxito con el id existente). Evento con `actor_type=AGENT`.
- `cancel_refund_flow` (determinista + HITL): identifica la solicitud propia en `PENDING_APPROVAL`, `interrupt()` de confirmación, y llama a la tool 9 `cancel_refund_request` (con `If-Match` = `version` del estado).
- **Tools 6 y 9 no se enlazan a ningún modelo**: solo las invocan nodos deterministas; test de registro del 09 se extiende.
- Interrupt vencido: si `resume` llega tras `expires_at` → volver a `check_eligibility` y emitir tarjeta nueva. Reevaluación BR-14 ocurre de todos modos dentro de `create_request`.

### API
| Ruta | Comportamiento |
| --- | --- |
| `POST /chat/threads/{id}/resume` | body `{interrupt_id, decision, edited_reason?}`; SSE; **409** si el interrupt ya se resolvió o no coincide el `interrupt_id` |
| `GET /chat/threads/{id}/pending-interrupt` | 200 con el payload de la tarjeta, 204 si no hay |
| `POST /chat/threads/{id}/messages` | con interrupt pendiente → **409 `INTERRUPT_PENDING`** con el payload (la tarjeta no desaparece sola); emite evento SSE `interrupt` cuando el grafo se detiene |

`GET /chat/threads/{id}/messages` incluye el interrupt pendiente si existe.

## Verificación

```bash
uv run pytest tests/agent -q -m "not live" && make check
# humo: crear hilo → pedir reembolso → recibir evento `interrupt` → POST /resume {"decision":"confirm"} → GET /refunds muestra channel=CHAT
```
