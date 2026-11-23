# ADR 0003 · Idempotencia en la creación y outbox para el pago

**Estado:** aceptada · **Specs:** 03, 04, 05 · **Challenge:** D7, §9 Consistencia, BR-14, BR-17…BR-23

## Contexto
Hay tres puntos donde repetir o intercalar operaciones cuesta dinero: (1) un doble clic o un reintento de red en `POST /refunds`; (2) dos supervisores que deciden a la vez la misma solicitud; (3) aprobar y pagar son operaciones distintas que pueden fallar por separado (un gateway caído no debe deshacer la aprobación, ni una aprobación perdida dejar un pago huérfano).

## Decisión
**Creación**
- `Idempotency-Key` obligatorio, `UNIQUE(customer_id, idempotency_key)` y `request_hash` (sha256 de compra y razón). Misma clave y mismo cuerpo → se repite la respuesta original (201); misma clave y otro cuerpo → 409 `IDEMPOTENCY_CONFLICT`.
- La fila de la compra se bloquea `FOR UPDATE` antes de insertar: los duplicados concurrentes esperan y reproducen. Un `IntegrityError` dentro de un savepoint relee por clave.
- Índice único parcial: una sola solicitud activa por compra.

**Decisión del supervisor**
- Bloqueo pesimista (`FOR UPDATE` + `populate_existing`) y optimista (`version` con `If-Match`). El perdedor de una carrera relee la fila ya decidida y recibe 409. Orden de comprobaciones fijo: permiso, autoaprobación (BR-20), estado (BR-17), versión (BR-18), nota (BR-19).

**Pago**
- `approve` cambia el estado, escribe el evento, la fila `refund_payouts` y el evento de **outbox** en la **misma transacción**: todo o nada.
- `PayoutWorker` reclama eventos con `FOR UPDATE SKIP LOCKED` (varios workers sin pisarse), una transacción por evento, contra un gateway idempotente por clave. Reintentos con backoff `base · 2^(n-1)`, máximo 3; luego `FAILED` + outbox `DEAD` (dead-letter, con log `event=payout_dead` y la métrica `refund_outbox_dead`). El supervisor puede reintentar (`retry-payout`).
- El `trace_id` de la petición viaja en el payload del outbox y el worker lo restaura, así el pago se rastrea hasta quien lo aprobó.

## Alternativas consideradas
1. **Llamar al gateway dentro de la petición de aprobación.** Latencia y fallos acoplados; una caída del gateway bloquea la aprobación o la deja a medias.
2. **Cola externa (Redis/Kafka/SQS).** Introduce la doble escritura base de datos + cola, justo lo que el outbox evita; Postgres ya está.
3. **Solo bloqueo optimista.** Detecta el conflicto pero deja ventanas donde dos transacciones leen "pendiente"; el `FOR UPDATE` lo hace determinista.

## Consecuencias
- (+) 20 `POST` concurrentes con la misma clave crean 1 solicitud; dos aprobaciones simultáneas dan un 200 y un 409 (tests).
- (+) Entrega *at-least-once* segura gracias a la idempotencia del gateway y a la clave de pago por solicitud.
- (−) El pago es asíncrono: la solicitud pasa por `APPROVED`/`PROCESSING` antes de `COMPLETED`; la UI lo muestra en vivo (SSE).
- (−) Se sondea la tabla (`PAYOUT_POLL_SECONDS`); con volumen alto habría que pasar a `LISTEN/NOTIFY` o a un broker.
