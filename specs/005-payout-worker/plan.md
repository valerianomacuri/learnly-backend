# Implementation Plan: 05 · Outbox y worker de pagos

**Feature**: `005-payout-worker` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/05-payout-worker.md`

Spec: [spec.md](spec.md) · Tareas: [tasks.md](tasks.md) · Constitution: [`.specify/memory/constitution.md`](../../.specify/memory/constitution.md)

## Cambios

- **Outbox:** en `approve` (spec 04) insertar `outbox_events(topic="refund.payout", payload={refund_id}, status=PENDING, available_at=now)` y `refund_payouts(status=PENDING, idempotency_key=str(refund_id), amount, method)` en la **misma** transacción que el cambio de estado. Si falla algo, no queda ni estado ni evento.
- **Gateway** (`app/workers/gateway.py`): `PaymentGateway` protocolo `pay(idempotency_key, method, amount, currency) -> provider_ref`; `SimulatedGateway` determinista (éxito por defecto; fallos configurables por env/`FAIL_RATE` y por un parámetro en tests; misma `idempotency_key` devuelve el mismo `provider_ref` sin pagar de nuevo).
- **Worker** (`python -m app.workers.payouts`, entry point `payouts-worker`; `--once` para tests): bucle que toma eventos `PENDING` con `available_at <= now` usando `SELECT … FOR UPDATE SKIP LOCKED`; transiciona `APPROVED → PROCESSING`; llama al gateway; éxito → `COMPLETED` y efectos BR-23 **atómicos por pago** (`purchase.status = REFUNDED`, revocar acceso — modelar como `course_enrollments.revoked_at` o campo en `purchases`, decidir mínimo —, si `PLATFORM_CREDITS` insertar `credit_ledger(+amount)`); error transitorio → reintento con backoff exponencial (`attempts`, `available_at = now + base * 2^n`, máx. 3, `last_error`); agotados → `FAILED`, outbox `DEAD` (dead-letter). Cada paso escribe `refund_events` (`actor_type=SYSTEM`).
- **Idempotencia:** si el worker muere tras pagar y antes de confirmar, la reejecución con la misma `idempotency_key` no duplica el pago ni el crédito (el ledger referencia `refund_id` con restricción única `(refund_id)` para créditos de reembolso).
- **`POST /supervisor/refunds/{id}/retry-payout`** (`refund:retry_payout`): solo `FAILED` (409 si no); `FAILED → PROCESSING`, nuevo outbox, 202.
- Al ser el hito sin LLM, ampliar `GET /me` (saldo de créditos) y el detalle del customer para mostrar `payout.status`.

## Verificación

```bash
uv run pytest tests/test_payouts_*.py -q && make check
uv run payouts-worker --once
# humo extremo a extremo por curl (guion en docs/demo-sin-llm.md): crear → aprobar → worker → GET /refunds/{id} == COMPLETED
```
