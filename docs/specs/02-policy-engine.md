# 02 · Política activa y `RefundPolicyEngine`

**Depende de:** 01 · **Refs:** challenge §4 (BR-01…BR-10), §7 (`EligibilityResult`), D1, D8, §8 (parámetros)

## Objetivo
Un motor determinista y testeable que decide la elegibilidad, alimentado por parámetros versionados que salen del front-matter de `policies/*.md`. Demostrable con el endpoint de elegibilidad contra los 13 casos semilla.

## Alcance
- Dentro: carga de front-matter → `policy_documents` + `PolicyConfig`; engine puro; `EligibilityResult`; endpoint de simulación; `eligibility_checks`.
- Fuera: chunking/embeddings (07); creación de solicitudes (03). La ingesta de este spec **solo** persiste documentos y parámetros (sin `policy_chunks`); el spec 07 reutiliza y extiende el mismo comando.

## Cambios

### Política (`src/app/policy/` — paquete de infraestructura, no `domain/`)
- `loader.py`: parsea los 6 `.md` (front-matter con `python-frontmatter` o `yaml` + Pydantic `PolicyDocumentMeta`: `doc_key`, `title`, `version`, `effective_date`, `language`, `tags`, `supersedes`, `params`, `reason_code_map`), calcula `checksum` (sha256 del archivo).
- `PolicyConfig` (Pydantic `extra="forbid"`, en `domain/policy_config.py`): `refund_window_days`, `max_progress_pct`, `abuse_flag_12m`, `abuse_block_12m`, `confirmation_ttl_min`, `sla_hours`, `one_active_request_per_purchase`, `rejected_is_final`, `policy_version`, `reason_code_map`. Faltante o mal tipado → `PolicyConfigError`; el arranque (`create_app`/lifespan) **falla** si no hay política ACTIVE completa.
- `PolicyRepository.get_active_config()` cacheado en memoria con invalidación por versión.
- CLI `python -m app.policy.load` (entry point `policies-load`): idempotente por checksum, activación atómica (nueva `ACTIVE`, anterior `SUPERSEDED`, misma transacción). `seed` lo invoca.

### Engine (`src/app/domain/eligibility.py`, puro)
```python
class RefundPolicyEngine:
    def __init__(self, config: PolicyConfig, clock: Callable[[], datetime]): ...
    def evaluate(self, facts: EligibilityFacts) -> EligibilityResult: ...
```
- `EligibilityFacts` (dataclass inmutable): compra (`customer_id`, `status`, `purchase_type`, `payment_method`, `purchased_at`, `amount`, `currency`), `progress_pct`, `certificate_issued_at`, `requesting_user_id`, `existing_requests` (estados), `completed_refunds_12m`. El servicio de aplicación **reúne** los hechos; el engine no toca la BD.
- Implementa BR-01…BR-10 con los códigos exactos del challenge. Notas: BR-02 inclusivo (`<=`) en UTC con reloj inyectable (naive datetime → error); BR-03/04/05 según estados; BR-05 `CANCELLED` no bloquea; BR-08 emite `SUBSCRIPTION_NOT_ELIGIBLE` o `THIRD_PARTY_PURCHASE`; BR-09 fija `refund_method` (CARD/PAYPAL → `ORIGINAL_PAYMENT`; GIFT/CREDITS/APPLE_IAP/GOOGLE_IAP → `PLATFORM_CREDITS`); BR-10 ≥ `abuse_block_12m` → `REFUND_ABUSE_LIMIT`, ≥ `abuse_flag_12m` → warning `HIGH_REFUND_FREQUENCY` (no bloquea, **no se muestra al customer**: el schema de customer lo omite).
- Se acumulan **todos** los `reason_codes` aplicables (orden estable); `PURCHASE_NOT_FOUND` corta (no se evalúa nada más).
- `EligibilityResult` tal cual §7.

### Servicio y endpoint
- `EligibilityService.check(principal, purchase_id, source)`: arma los hechos (repos de compras, progreso, solicitudes, conteo 12 m), evalúa, **persiste siempre** una fila en `eligibility_checks` (`source` = `SIMULATION|PREVIEW|CREATE_ATTEMPT|AGENT`) y devuelve el resultado. Compra ajena → resultado `PURCHASE_NOT_FOUND` (200 con `eligible:false`, misma forma que inexistente).
- `GET /api/v1/purchases/{id}/refund-eligibility` (`refund:simulate`, solo customer).
- Sustituir el `TODO(spec-02)` de `refund_deadline` por `PolicyConfig`.

## Tareas
- [ ] `PolicyConfig`, loader, tablas `policy_documents` (ya del 01) y CLI; test "falta un param → no arranca".
- [ ] **Tests del engine primero**: tabla parametrizada de los 13 casos + Hypothesis en bordes (30 d −1 s elegible, exacto elegible, +1 s no; progreso 30/31; 4/5 reembolsos; 2/3 flag).
- [ ] Implementar engine hasta verde.
- [ ] `EligibilityService`, ruta, schemas (customer sin warnings), test de persistencia de `eligibility_checks`.
- [ ] Cobertura ≥ 85 % en `domain/` (`--cov=app.domain`, añadir `pytest-cov` y `hypothesis` a dev).

## Criterios de aceptación
- Los 13 casos semilla producen exactamente el resultado de la tabla del challenge §9.
- Cambiar `refund_window_days` en el front-matter y recargar cambia el resultado sin tocar código.
- Compra ajena e inexistente: respuesta indistinguible.
- `mypy --strict` pasa en `app.domain`.

## Verificación
```bash
uv run pytest tests/unit/test_eligibility.py tests/test_eligibility_api.py --cov=app.domain --cov-fail-under=85
uv run policies-load && uv run seed
curl -H "Authorization: Bearer $CUST1" localhost:8000/api/v1/purchases/<id-fuera-de-plazo>/refund-eligibility
```
