# Feature Specification: 05 · Outbox y worker de pagos

**Feature**: `005-payout-worker` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/05-payout-worker.md`

**Depende de:** 04 · **Refs:** challenge §4 (BR-22, BR-23), D7, §9 (consistencia, resiliencia)

## Objetivo

Aprobar y pagar son operaciones separadas. Al aprobar se escribe un evento de outbox en la misma transacción; un worker ejecuta el pago contra un gateway simulado con idempotencia y reintentos. **Hito: el backend queda completo y funcional sin LLM.**

## Alcance

- Dentro: outbox, worker, gateway simulado, `retry-payout`, efectos de `COMPLETED` (compra `REFUNDED`, acceso revocado, `credit_ledger`).
- Fuera: notificaciones por email (extensión opcional); métricas (13).

## Criterios de aceptación

- Aprobar → worker → `COMPLETED`; `purchase.status = REFUNDED`; saldo de créditos sube solo para `PLATFORM_CREDITS`.
- Gateway que falla 3 veces → `FAILED`; `retry-payout` lo completa.
- Doble ejecución del mismo outbox no duplica pagos ni créditos.
- Ciclo completo demostrable solo con API (sin LLM): preview → crear → aprobar → worker → completado.
