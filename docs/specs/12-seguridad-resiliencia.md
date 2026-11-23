# 12 · Seguridad, límites y resiliencia

**Depende de:** 10 (y 11 para validar en UI) · **Refs:** challenge §2 (reglas del agente 5 y 6), §9 (autorización, injection, privacidad, resiliencia, límites). Usar el subagente `ai-engineer` para el set adversarial.

## Objetivo
Endurecer lo ya construido: límites de uso, guardrails reales, privacidad, suite BOLA/IDOR exhaustiva y degradación controlada cuando fallan LLM o vector store.

## Alcance
- Dentro: Redis, rate limit, presupuesto de tokens, guardrails de entrada/salida, PII, set adversarial, suite BOLA, circuit breaker, modelo de respaldo, degradación.
- Fuera: observabilidad/trazas (13).

## Cambios

### Redis y límites
- `docker-compose.yml`: servicio `redis`; `REDIS_URL` en settings; dependencia `redis`.
- `core/rate_limit.py`: ventana deslizante (script Lua o `INCR`+`EXPIRE`) con dependencia FastAPI `rate_limit(name, limit, window)`. Chat: 20 mensajes/min por customer (`POST /chat/threads/{id}/messages` y `/resume`); `POST /refunds`: 5/hora por customer. Respuesta 429 problem+json con `Retry-After`. Sin Redis → fallo **abierto solo para lectura**, cerrado para escritura (documentar y testear).
- Presupuesto de tokens por hilo (`THREAD_TOKEN_BUDGET`): contador en Redis/estado; al excederlo el hilo se corta con mensaje claro y SSE `error{code:"TOKEN_BUDGET_EXCEEDED"}`.
- Locks de Redis solo si hace falta (p. ej. un solo run de grafo por hilo: 409 si hay otro en curso).

### Guardrails (`agent/guardrails/`)
- **Entrada:** `injection.py` (heurísticas + clasificador LLM barato con salida estructurada; patrones "ignora instrucciones", rol falso, exfiltración de prompt), límite de longitud, enmascarado de PII (email, tarjeta, teléfono) antes de logs/LLM. Si bloquea → `respond` con mensaje seguro y sin tool calls.
- **Salida:** bloquea ids de supervisor, notas internas, emails/datos de otros usuarios; exige cita en afirmaciones de política; cifras contra `PolicyConfig`.
- **Contenido no confiable:** envolver chunks y texto del cliente en delimitadores (`<untrusted>`) con instrucción de sistema; test con documento envenenado.
- `core/privacy.py` (ya con el enmascarado de email del 04): filtro de logging que enmascara emails y tokens en **todos** los logs.

### Autorización
- Suite `tests/security/test_bola.py` **parametrizada sobre todas las rutas** que reciben un id (compras, refunds, events, cancel, chat threads/messages/resume/pending-interrupt): customer A sobre recurso de B → 404; customer en `/supervisor/*` → 403; supervisor en `/chat/*` y `POST /refunds` → 403. Test que **enumera las rutas del OpenAPI** y falla si una ruta nueva no está en la tabla de permisos esperados (lista de verificación viva).

### Resiliencia
- `agent/llm.py`: timeouts, reintentos con backoff y **circuit breaker** (estado cerrado/abierto/semiabierto, en memoria por proceso) y **modelo de respaldo** (`LLM_FALLBACK_MODEL`, también OpenAI).
- Con el LLM caído: chat responde con degradación explícita y el formulario manual **sigue funcionando** (el preview/create no dependen del LLM — test de integración que fuerza el breaker abierto).
- Con el vector store caído (`policy_chunks` inaccesible): `policy_qa` informa que no puede consultar políticas y ofrece `handoff`; `/refunds/preview` sigue (citas por mapa).
- Worker de pagos: dead-letter ya existe (05); añadir alarma/log estructurado en `DEAD`.

## Tareas
- [x] Redis + `rate_limit` + tests (429 y `Retry-After`; ventana; clave por customer; sin Redis).
- [x] Presupuesto de tokens y corte de hilo (test con `FakeChatModel` que reporta uso).
- [x] Guardrails de entrada/salida con tests unitarios por patrón.
- [x] **Set adversarial** (`tests/agent_evals/adversarial.jsonl`, ≥ 40 casos: injection directa, indirecta vía `reason_text`, vía chunk envenenado, suplantación de rol/`customer_id` en el texto, petición de aprobar, de leer notas, de ver datos ajenos, exfiltración del prompt): para cada uno, **cero escrituras** (`refund_requests`, `refund_events`, `credit_ledger` sin cambios) y ninguna fuga en la salida.
- [x] Suite BOLA/IDOR parametrizada y test de enumeración de rutas.
- [x] Circuit breaker + fallback; tests de degradación (LLM y vector store).
- [x] Test de privacidad: capturar logs y prompts enviados al LLM en una conversación con email y nota interna; assert de ausencia.

## Criterios de aceptación
- 100 % de los intentos del set adversarial no producen escrituras ni fugas.
- 21.º mensaje del minuto → 429 con `Retry-After`; 6.º `POST /refunds` en una hora → 429.
- Con el LLM caído, el formulario completo (preview → crear) funciona.
- Una ruta nueva sin entrada en la tabla de permisos hace fallar la suite.

## Verificación
```bash
docker compose up -d db redis
uv run pytest tests/security tests/agent_evals -q -m "not live" && make check
```
