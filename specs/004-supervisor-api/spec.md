# Feature Specification: 04 · API del supervisor

**Feature**: `004-supervisor-api` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/04-supervisor-api.md`

**Depende de:** 03 · **Refs:** challenge §2 (permisos supervisor), §4 (BR-17…BR-21), §5 (dashboard)

## Objetivo

El supervisor revisa la cola y decide (aprobar/rechazar) con control de concurrencia optimista. Aprobar deja la solicitud en `APPROVED`; el pago se ejecuta en el spec 05.

## Alcance

- Dentro: cola con filtros, detalle, approve/reject, notas internas, timeline completo, stats, SLA, flag de riesgo, enmascarado de PII.
- Fuera: outbox/pago (05 — aquí `approve` solo cambia estado y escribe el evento; el spec 05 añade la fila de outbox en la **misma** transacción), SSE (06).

## Criterios de aceptación

- Dos supervisores aprueban simultáneamente la misma solicitud: exactamente un 200 y un 409.
- Customer recibe 403 en cualquier ruta `/supervisor/*`; supervisor sobre su propia solicitud → 403 `SELF_APPROVAL_FORBIDDEN`.
- Rechazar con nota de 9 caracteres → 422.
- El email nunca aparece sin enmascarar en las respuestas del supervisor.
