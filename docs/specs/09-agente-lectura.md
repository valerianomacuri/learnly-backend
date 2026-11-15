# 09 · Agente: tools y grafo de lectura

**Depende de:** 07 (RAG), 03 (servicios) · **Refs:** challenge §2 (reglas del agente), §5 (chat), §6 (estado, nodos, persistencia), §7 (tools). Usar el subagente `ai-engineer`.

## Objetivo
Chat funcional para los caminos de **solo lectura**: preguntas de política con citas, estado de solicitudes, fuera de alcance y handoff. Establece la frontera de seguridad del agente (identidad inyectada, allowlist de tools) antes de darle ninguna capacidad de escritura.

## Alcance
- Dentro: `Principal` del agente, tools de lectura (1–5, 7, 8) + `escalate_to_human` (10), grafo parcial, checkpointer, endpoints de hilos y mensajes con SSE, degradación de clasificador.
- Fuera: flujo de reembolso y HITL (10); tools de escritura 6 y 9 (10); guardrails avanzados (12 — aquí solo guardrails mínimos: longitud y filtro de salida básico).

## Cambios

### Dependencias y config
`langgraph`, `langgraph-checkpoint-postgres`, `langchain-core`, `langchain-openai`. `LLM_MODEL`, `LLM_TIMEOUT_S`, `LLM_MAX_RETRIES`. Factory `agent/llm.py` (`get_chat_model()`), con `FakeChatModel` guionable para tests (respuestas y llamadas a tools predefinidas).

### Identidad y tools (`src/app/agent/`)
- `agent/principal.py`: `Principal(user_id, role, acting_via="agent")` construido **solo** desde `config["configurable"]` (JWT → API). Permisos efectivos = subconjunto del customer (`purchase:read_own`, `refund:read_own`, `policy:read`, `refund:simulate`). El agente nunca recibe `customer_id`/`role` como argumento del LLM (regla 1).
- `agent/tools/`: una función por tool, adaptador fino sobre servicios existentes (`PolicyRetriever`, `PurchaseService`, `EligibilityService`, `RefundService` lectura). `ToolResult{ok, data, error{code, message, retryable}}`; errores de dominio como datos, de infraestructura reintentables/escalables. Esquemas Pydantic `extra="forbid"` con descripciones para el modelo. Tools: `search_refund_policy` (top_k ≤ 6, chunks marcados no confiables), `list_my_purchases` (máx. 20, proyección mínima), `get_purchase_details` (ajena → `PURCHASE_NOT_FOUND`), `check_refund_eligibility` (spec 10 lo usa), `prepare_refund_confirmation` (spec 10), `list_my_refunds`, `get_refund_status` (sin notas), `escalate_to_human` (marca `chat_threads.status = needs_human`).
- `agent/tools/registry.py`: **allowlist** explícita `LLM_BOUND_TOOLS`; **test** que falla si alguna tool enlazada tiene un parámetro `customer_id` o `role`, y otro que falla si el registro contiene aprobación/pago/notas.
- Cada llamada con efecto deja `refund_event` o registro con `actor_type=AGENT`, `acting_for`, `trace_id` (solo `escalate_to_human` en este spec escribe).

### Grafo (`agent/graph.py`, `agent/state.py`, `agent/nodes/`)
- `SupportState` (§6) con `add_messages`; `customer_id/role/request_id` fuera del estado.
- Nodos (un archivo cada uno): `entry_router` (si `awaiting` → `collect_refund_info`, aquí stub que apunta a `classify`; el 10 lo activa), `guard_input` (longitud, enmascarado básico de PII, marcador de contenido no confiable; el detector de injection llega en 12), `classify_intent` (salida estructurada Pydantic, T=0, 6 intenciones; si falla 2 veces → clasificador por palabras clave), `policy_qa` (usa `rag.answer`; abstención → `handoff`), `refund_status` (sub-agente ReAct acotado con `bind_tools` de lectura, `tool_budget=6`), `handoff`, `guard_output` (filtra ids de supervisor, notas internas, emails ajenos; verifica citas presentes si hay afirmación de política), `respond`.
- Intenciones `refund_request` y `cancel_refund` rutean provisionalmente a un mensaje "disponible en el formulario" hasta el spec 10.
- `AsyncPostgresSaver` como checkpointer (`thread_id = chat_threads.id`); `recursion_limit=25`; `RetryPolicy` con backoff en errores transitorios; timeout por nodo LLM.

### API (`api/routes/chat.py`, permiso `chat:use`, propiedad del hilo verificada)
| Ruta | Notas |
| --- | --- |
| `POST /chat/threads` → 201 | crea `chat_threads` y devuelve `thread_id` |
| `GET /chat/threads` | solo propios |
| `GET /chat/threads/{id}/messages` | historial leyendo el checkpointer; ajeno → 404 |
| `POST /chat/threads/{id}/messages` | SSE: eventos `token`, `tool_call`, `tool_result`, `final`, `error` (`interrupt` lo añade el 10). Traducción de `astream(stream_mode=["messages","updates","custom"])`. 429 lo añade el 12 |

`agent/sse.py`: serialización de eventos (`event:` + `data:` JSON con `id` incremental), test de contrato.

## Tareas
- [ ] Tests primero: registro de tools (sin `customer_id`/`role`, sin tools prohibidas), BOLA de tools (ajeno → `PURCHASE_NOT_FOUND`), `ToolResult` de error.
- [ ] Factory LLM + `FakeChatModel`; fixtures de grafo.
- [ ] Servicios/tools de lectura y `escalate_to_human`.
- [ ] Nodos y ensamblado del grafo; checkpointer Postgres (tablas del saver vía `setup()` en migración/arranque).
- [ ] Endpoints + SSE + test de contrato de eventos.
- [ ] Trayectorias con LLM falso: pregunta de política con cita; estado de una solicitud; fuera de alcance → handoff; sin evidencia → "no sé" + handoff; clasificador con fallo ×2 → palabras clave.
- [ ] Test de aislamiento: hilo ajeno → 404; el supervisor no puede usar `/chat` (403).
- [ ] Prueba manual con OpenAI real (`live`).

## Criterios de aceptación
- Pregunta de política: respuesta con citas reales y sin cifras fuera de `PolicyConfig`.
- "¿Cómo va mi solicitud?" lista solo solicitudes propias, sin notas internas.
- Ninguna tool enlazada al LLM acepta `customer_id` ni `role` (test automático).
- Recargar la página y pedir `/messages` reconstruye el historial desde el checkpointer.

## Verificación
```bash
uv run pytest tests/agent tests/test_chat_api.py -q -m "not live" && make check
# humo SSE
curl -N -X POST -H "Authorization: Bearer $CUST1" -H 'Content-Type: application/json' \
  localhost:8000/api/v1/chat/threads/$TID/messages -d '{"content":"¿Cuántos días tengo para pedir un reembolso?"}'
```
