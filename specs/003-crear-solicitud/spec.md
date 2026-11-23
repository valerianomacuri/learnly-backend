# Feature Specification: 03 · Crear solicitud (canal formulario)

**Feature**: `003-crear-solicitud` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/03-crear-solicitud.md`

**Depende de:** 02 · **Refs:** challenge §4 (BR-11…BR-16, BR-24), §5 (reembolsos), D3, D4, D6

## Objetivo

Un único punto de entrada de dominio, `RefundService.create_request()`, usado por el endpoint manual (y más adelante por la tool del agente). Flujo preview → confirmación → creación con idempotencia, reevaluación transaccional y máquina de estados.

## Alcance

- Dentro: preview, creación, listado/detalle/timeline del customer, cancelación, problem+json, máquina de estados.
- Fuera: aprobación (04), pago (05), citas reales en el preview (07; ahora devuelve `citations` vía `reason_code_map` solo para inelegibles, y `[]` si elegible), rate limit (12).

## Criterios de aceptación

- 20 `POST /refunds` concurrentes con la misma `Idempotency-Key` crean exactamente 1 solicitud y todos reciben la misma respuesta.
- Inelegible: 422 con `reason_codes`, 0 filas en `refund_requests`, ≥1 en `eligibility_checks`.
- Si el plazo vence entre `preview` y `POST`, el `POST` responde 422 (reevaluación).
- Un customer nunca ve `internal_notes` ni eventos internos.
