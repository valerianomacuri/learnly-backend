# Feature Specification: 01 · Modelo de datos, semilla y lectura de compras

**Feature**: `001-modelo-datos-semilla` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/01-modelo-datos-semilla.md`

**Depende de:** 00 · **Refs:** challenge §3, §5 (compras), §9 (datos semilla, auditoría)

## Objetivo

Crear todo el esquema transaccional del dominio, la semilla de los 13 casos borde y los endpoints de lectura de compras con aislamiento por dueño. Demostrable con `uv run seed` + curl.

## Alcance

- Dentro: modelos, migración `0002`, trigger append-only, semilla, `GET /me`, `GET /purchases`, `GET /purchases/{id}`, alias `/healthz`.
- Fuera: `policy_chunks` (spec 07), lógica de elegibilidad (02), escritura de solicitudes (03). Las tablas de pago/outbox/créditos se crean ya pero no se usan hasta 05.

## Criterios de aceptación

- Intentar modificar un `refund_events` falla **a nivel de base de datos**.
- 404 idéntico (mismo cuerpo) para compra inexistente y ajena.
- `uv run seed` es idempotente y `--reset` recrea los 13 casos con fechas relativas.
- Un supervisor recibe 403 en `GET /purchases` (no es su recurso).
