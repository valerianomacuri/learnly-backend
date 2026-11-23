# Tasks: 02 · Política activa y `RefundPolicyEngine`

**Feature**: `002-policy-engine` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/02-policy-engine.md`

## Tareas

- [x] `PolicyConfig`, loader, tablas `policy_documents` (ya del 01) y CLI; test "falta un param → no arranca".
- [x] **Tests del engine primero**: tabla parametrizada de los 13 casos + Hypothesis en bordes (30 d −1 s elegible, exacto elegible, +1 s no; progreso 30/31; 4/5 reembolsos; 2/3 flag).
- [x] Implementar engine hasta verde.
- [x] `EligibilityService`, ruta, schemas (customer sin warnings), test de persistencia de `eligibility_checks`.
- [x] Cobertura ≥ 85 % en `domain/` (`--cov=app.domain`, añadir `pytest-cov` y `hypothesis` a dev).
