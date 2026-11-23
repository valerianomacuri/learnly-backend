# Feature Specification: 02 · Política activa y `RefundPolicyEngine`

**Feature**: `002-policy-engine` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/02-policy-engine.md`

**Depende de:** 01 · **Refs:** challenge §4 (BR-01…BR-10), §7 (`EligibilityResult`), D1, D8, §8 (parámetros)

## Objetivo

Un motor determinista y testeable que decide la elegibilidad, alimentado por parámetros versionados que salen del front-matter de `policies/*.md`. Demostrable con el endpoint de elegibilidad contra los 13 casos semilla.

## Alcance

- Dentro: carga de front-matter → `policy_documents` + `PolicyConfig`; engine puro; `EligibilityResult`; endpoint de simulación; `eligibility_checks`.
- Fuera: chunking/embeddings (07); creación de solicitudes (03). La ingesta de este spec **solo** persiste documentos y parámetros (sin `policy_chunks`); el spec 07 reutiliza y extiende el mismo comando.

## Criterios de aceptación

- Los 13 casos semilla producen exactamente el resultado de la tabla del challenge §9.
- Cambiar `refund_window_days` en el front-matter y recargar cambia el resultado sin tocar código.
- Compra ajena e inexistente: respuesta indistinguible.
- `mypy --strict` pasa en `app.domain`.
