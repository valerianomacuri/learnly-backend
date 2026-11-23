# Tasks: 12 · Seguridad, límites y resiliencia

**Feature**: `012-seguridad-resiliencia` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/12-seguridad-resiliencia.md`

## Tareas

- [x] Redis + `rate_limit` + tests (429 y `Retry-After`; ventana; clave por customer; sin Redis).
- [x] Presupuesto de tokens y corte de hilo (test con `FakeChatModel` que reporta uso).
- [x] Guardrails de entrada/salida con tests unitarios por patrón.
- [x] **Set adversarial** (`tests/agent_evals/adversarial.jsonl`, ≥ 40 casos: injection directa, indirecta vía `reason_text`, vía chunk envenenado, suplantación de rol/`customer_id` en el texto, petición de aprobar, de leer notas, de ver datos ajenos, exfiltración del prompt): para cada uno, **cero escrituras** (`refund_requests`, `refund_events`, `credit_ledger` sin cambios) y ninguna fuga en la salida.
- [x] Suite BOLA/IDOR parametrizada y test de enumeración de rutas.
- [x] Circuit breaker + fallback; tests de degradación (LLM y vector store).
- [x] Test de privacidad: capturar logs y prompts enviados al LLM en una conversación con email y nota interna; assert de ausencia.
