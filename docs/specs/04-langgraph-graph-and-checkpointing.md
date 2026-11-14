# Spec 04 — Grafo LangGraph, checkpointing, estado y chat (API + widget)

Fuente: `challenge.md` §5 (chat), §6 completo, §9 (agente seguro, injection, privacidad, límites, resiliencia, testing, evals del agente) y "Frontend mínimo → chat". Depende de **01** (servicios, `chat_threads`, rate limit), **02** (tools, `ToolContext`, `OutputGuard`, `SimpleSupportAgent`, `llm.py`) y **03** (`PolicyAnswerer`, `CitationService`, retriever).

## 1. Objetivo

Un único grafo LangGraph para el customer, **híbrido**: el camino que mueve dinero (recolectar datos → elegibilidad → confirmación → creación) es un workflow explícito con nodos deterministas; el LLM solo interviene donde hay lenguaje natural. Con **HITL** real (`interrupt`), persistencia en Postgres (el cliente puede cerrar el navegador y retomar, incluso con una confirmación pendiente), streaming SSE, guardrails de entrada y salida, degradación ante fallos y un widget de chat en el frontend.

Invariantes (se prueban):
- El LLM nunca decide elegibilidad ni crea/cancela: `create_refund_request` y `cancel_refund_request` las invoca un nodo determinista con argumentos tomados del **estado** y solo después de un `interrupt` resuelto con `confirm`.
- `customer_id`, `role`, `thread_id`, `request_id` llegan por `config["configurable"]`, nunca por estado ni por el LLM.
- Contenido del cliente y chunks del RAG son datos, no instrucciones.

## 2. Alcance

**Dentro:** `app/ai/graph/` (estado, nodos, aristas, checkpointer, compilación), `app/ai/guardrails/` (entrada completa + ampliación de salida), `app/api/routes/chat.py` (+ servicio de chat), SSE, rate limit y presupuesto de tokens por hilo, widget de chat (Tailwind) y tarjeta HITL, trayectorias de test con LLM falso y 30 guiones de eval del agente.

**Fuera:** observabilidad (trazas Langfuse/LangSmith, métricas) → fase posterior; Redis; `Last-Event-ID`; el agente del supervisor (no existe).

## 3. Estructura

```text
src/app/ai/graph/
  state.py            # SupportState, enums (Intent, Awaiting), ConfirmationPayload, ResumeValue
  builder.py          # build_graph(checkpointer, deps) -> CompiledStateGraph
  routing.py          # funciones de arista condicional (puras, testeables)
  checkpointer.py     # create_checkpointer(settings) -> AsyncPostgresSaver (+ setup())
  runtime.py          # GraphRuntime: astream/resume/get_state, traducción a eventos SSE, límites
  events.py           # SSEEvent (token|tool_call|tool_result|interrupt|final|error), serialización
  fallback.py         # clasificador por palabras clave (degradación)
  nodes/
    entry_router.py  guard_input.py  classify_intent.py  policy_qa.py
    collect_refund_info.py  check_eligibility.py  explain_ineligible.py
    request_confirmation.py  create_refund.py  refund_status.py
    cancel_refund_flow.py  handoff.py  guard_output.py  respond.py
src/app/ai/guardrails/
  input.py            # InputGuard: PII masking, injection, longitud
  output.py           # (de 02) ampliado
  pii.py              # enmascarado de emails/teléfonos/tarjetas
src/app/ai/prompts/   # classify_intent.v1.md, collect_info.v1.md, explain_ineligible.v1.md, respond.v1.md, refund_status.v1.md
src/app/services/chat_service.py      # hilos, mensajes (lee checkpointer), límites, rate/tokens
src/app/api/routes/chat.py            # /chat/*
tests/ai/graph/        # test_routing, test_trajectories, test_interrupt, test_checkpoint, test_guardrails, test_sse_contract, test_chat_api
src/app/ai/evals/agent/  scripts.jsonl (30 guiones), runner.py, metrics.py
learnly-frontend/src/features/chat/  # ChatPage, ChatWidget, MessageList, Composer, ConfirmationCard (reutiliza components/), useChatStream.ts, api/chat.ts
```

## 4. Estado (`SupportState`)

`TypedDict` (LangGraph) con reducers explícitos:

| Campo | Tipo | Reducer | Uso |
| --- | --- | --- | --- |
| `messages` | `list[AnyMessage]` | `add_messages` | conversación (con mensajes de usuario **ya enmascarados** por `guard_input` para el LLM; el original no se persiste en el estado) |
| `intent` | `Intent` (`policy_question`, `refund_request`, `refund_status`, `cancel_refund`, `out_of_scope`, `human_handoff`) \| None | reemplazo | ruteo |
| `awaiting` | `None \| "purchase" \| "reason"` | reemplazo | si el turno anterior terminó con pregunta, el siguiente mensaje entra directo a `collect_refund_info` |
| `selected_purchase_id` | `UUID \| None` | reemplazo | compra elegida **y verificada** contra el customer |
| `reason_category`, `reason_text` | `ReasonCategory \| None`, `str \| None` | reemplazo | razón extraída |
| `eligibility` | `EligibilityView \| None` | reemplazo | salida del engine con `reason_codes` y citas |
| `pending_confirmation` | `{hash, expires_at, kind: "create"\|"cancel", target_id?, expected_version?}` \| None | reemplazo | ata la confirmación al payload exacto |
| `refund_id` | `UUID \| None` | reemplazo | resultado de la creación |
| `citations` | `list[Citation]` | `add` con dedupe | fuentes del RAG de la respuesta |
| `guardrail_flags` | `list[str]` | `add` | resultados de guardrails (`PII_MASKED`, `INJECTION_SUSPECTED`, `OUTPUT_BLOCKED`, …) |
| `tool_budget`, `retries` | `int` | reemplazo | límites **por turno** (se reinician en `entry_router`) |
| `turn_tokens`, `thread_tokens` | `int` | suma | presupuesto de tokens |
| `reply` | `str \| None` | reemplazo | borrador de respuesta del turno para `guard_output` |

`customer_id`, `role` y `request_id` **no** están en el estado. Un test compara las claves de `SupportState` con una lista prohibida. El estado debe ser serializable por el checkpointer (UUID/datetime/Pydantic con el serde de LangGraph; sin objetos de sesión).

`ConfirmationPayload` (valor del `interrupt`): `{kind:"refund_confirmation"|"cancel_confirmation", interrupt_id, purchase|refund (resumen), amount, currency, refund_method, reason, window_deadline, policy_citations, hash, expires_at}`. `ResumeValue` (Pydantic, `extra="forbid"`): `{interrupt_id: str, decision: "confirm"|"cancel"|"edit", edited_reason?: str (20–1000)}`; `edited_reason` solo válido con `edit`.

## 5. Nodos

Convención: cada archivo exporta una función async `node(state, config) -> dict` (actualización parcial del estado) y registra sus dependencias vía un `GraphDeps` inyectado en `build_graph` (LLMs, runtime de tools, `PolicyAnswerer`, `CitationService`, `Clock`, `OutputGuard`/`InputGuard`). Los nodos con LLM usan `invoke_llm` (breaker/timeout de 02) y llevan `RetryPolicy` solo para errores transitorios.

| Nodo | Tipo | Comportamiento |
| --- | --- | --- |
| `entry_router` | determinista | Reinicia `tool_budget=6`, `retries=0`, `turn_tokens=0`, `reply=None`. Si `awaiting` está definido → `collect_refund_info`; si no → `guard_input`. Si `thread_tokens` ≥ presupuesto del hilo → `respond` con mensaje de límite (§8) |
| `guard_input` | determinista + clasificador | Trunca a 2000 caracteres (flag), **enmascara PII** (email, teléfono, tarjeta) antes de que llegue al LLM/trazas, detecta injection (reglas + clasificador del modelo rápido con salida estructurada `{suspicious: bool}`; si el clasificador falla se queda solo con las reglas). Bloqueado → `respond` con mensaje seguro; "sospechoso" no bloquea pero marca `INJECTION_SUSPECTED` y el resto del grafo lo trata como dato |
| `classify_intent` | LLM, salida estructurada | `IntentResult{intent, course_query?}` (Pydantic, temp 0, modelo rápido, ve los últimos 6 mensajes). Falla 2 veces → `fallback.keyword_classifier` (palabras clave ES/EN: reembols/refund, estado/status, cancel, humano/agent…; default `policy_question`) y flag `CLASSIFIER_FALLBACK` |
| `policy_qa` | subgrafo RAG | Llama a `PolicyAnswerer.answer`; guarda `reply`, `citations`. `abstained`/`needs_handoff` → `handoff`. `RetrieverUnavailable` → mensaje "no puedo consultar las políticas ahora" + oferta de handoff (sin inventar) |
| `collect_refund_info` | LLM + tools de lectura | Con `TOOLSETS["collect_refund_info"]` (`list_my_purchases`, `get_purchase_details`) y modelo principal. Resuelve la compra (si hay varias candidatas pregunta cuál: `awaiting="purchase"`) y extrae `reason_category`/`reason_text` con salida estructurada (`ReasonExtraction`). La compra elegida se **verifica** con `get_purchase_details` (propiedad) antes de guardarla. Si falta compra o razón (BR-12: 20–1000 chars, ≥ 3 palabras distintas) → fija `awaiting` y termina el turno con la pregunta (**nunca** llega al paso de confirmación sin razón). Bucle limitado por `tool_budget` |
| `check_eligibility` | determinista | Llama a la tool 4 (`check_refund_eligibility`) con `selected_purchase_id`; guarda `eligibility`. Rama: elegible → `request_confirmation`; no → `explain_ineligible`. Error de infra → reintento/`handoff` |
| `explain_ineligible` | LLM + RAG | Citas deterministas con `CitationService.citations_for(reason_codes, include_semantic=True)`; el LLM **solo narra** los `reason_codes` y ofrece alternativas válidas (créditos si aplica, handoff). No crea nada. `reply` con citas |
| `request_confirmation` | **HITL** | **Primera línea del nodo: `interrupt(payload)`** (ver §6). Antes llama (en una función auxiliar *idempotente y sin efectos*, ejecutada en un nodo previo `prepare_confirmation` o memoizada en el estado) a la tool 5 para armar `payload` y `hash`. Tras reanudar: `confirm` → `create_refund`; `cancel` → `respond` ("no se creó nada"); `edit` → `collect_refund_info` con `awaiting="reason"`; confirmación vencida → ver §6 |
| `create_refund` | determinista | Llama a la tool 6 con args **del estado** (`selected_purchase_id`, `reason_*`, `pending_confirmation.hash`, `confirmed_at`). `Idempotency-Key = sha256(thread_id + ":" + hash)` (dentro de la tool). Maneja: `REFUND_NOT_ELIGIBLE` (la elegibilidad cambió entre la tarjeta y la confirmación → `explain_ineligible`), `ACTIVE_REFUND_EXISTS`/`replayed` (respuesta amable con el `refund_id`), `CONFIRMATION_INVALID`, infra (`retries`, luego `handoff`). Guarda `refund_id` |
| `refund_status` | sub-agente de lectura | Usa `SimpleSupportAgent(toolset="refund_status")` (02) sobre las tools 7 y 8; resume estado y timeline público. Nunca ve notas internas (las tools no las devuelven) |
| `cancel_refund_flow` | determinista + HITL | Identifica la solicitud (tool 7; si hay varias en `PENDING_APPROVAL` pregunta cuál), arma un `ConfirmationPayload(kind="cancel_confirmation")` con `expected_version`, **primera línea `interrupt`**, y tras `confirm` llama a la tool 9. Si ya no está en `PENDING_APPROVAL` explica el estado (409) |
| `handoff` | determinista | Tool 10 (`escalate_to_human`) con `summary` generado por el modelo rápido a partir de los últimos mensajes **enmascarados**; marca el hilo `NEEDS_HUMAN`; `reply` con el canal de soporte |
| `guard_output` | determinista | `OutputGuard` sobre `reply`: ids que no sean del customer, emails ajenos, texto de notas internas/ids de supervisor, **afirmaciones de política sin cita** (si el `reply` menciona cifras de política y `citations` está vacío → reemplaza por la respuesta verificada o por handoff) y `NumericVerifier` de 03 sobre cualquier cifra de política. Bloqueo → mensaje seguro + `OUTPUT_BLOCKED` |
| `respond` | determinista (+ LLM opcional) | Si el borrador viene de un nodo de plantilla, lo emite tal cual; si viene del LLM, lo pasa por un redactor final breve (modelo principal, tono y idioma del cliente) **sin acceso a tools**. Añade el `AIMessage` final a `messages` |

Detalle importante de `request_confirmation`: LangGraph **reejecuta el nodo desde su inicio al reanudar**, así que todo lo anterior a `interrupt()` debe ser puro/idempotente. Para evitar trabajo previo repetido y efectos, el payload se construye en un nodo anterior (`prepare_confirmation`: llama a la tool 5, guarda `pending_confirmation` y el payload serializable en el estado) y `request_confirmation` solo contiene `decision = interrupt(state["confirmation_payload"])` seguido de la lógica de decisión. (`prepare_confirmation` es un decimoquinto nodo auxiliar; el diagrama del challenge lo agrupa dentro de `request_confirmation`.)

## 6. Ruteo, interrupt y reanudación

1. **Aristas condicionales** (`routing.py`, funciones puras con test de tabla): `guard_input` (bloqueado / ok), `classify_intent` (6 intenciones), `collect_refund_info` (falta dato / completo), `check_eligibility` (elegible / no), `request_confirmation` (confirmar / cancelar / editar). Todas las ramas terminales convergen en `guard_output → respond → END`.
2. **Interrupt**: `interrupt(payload)` es lo primero que ejecuta su nodo. `payload.interrupt_id` = id del interrupt de LangGraph; `hash` = sha256 del payload canónico de la tool 5 (mismo cálculo que el token del formulario de 01); `expires_at = now + confirmation_ttl_min` (de `PolicyConfig`).
3. **Reanudación**: `Command(resume=ResumeValue)`, validado con Pydantic **antes** de reanudar (en `chat_service`): `interrupt_id` debe coincidir con el pendiente del hilo (si el interrupt ya se resolvió → 409), `decision` válida, `edited_reason` solo con `edit`. `edit` re-valida la razón (BR-12) y vuelve a `collect_refund_info` con el nuevo texto; luego `check_eligibility` y una tarjeta **nueva**.
4. **Mensaje libre con interrupt pendiente**: `POST /chat/threads/{id}/messages` responde **409 `INTERRUPT_PENDING`** con el payload de la tarjeta (el mensaje no se procesa); la tarjeta no desaparece sola.
5. **Interrupt vencido**: si `confirmed_at > expires_at`, el nodo no crea nada: invalida `pending_confirmation`, vuelve a `check_eligibility` (el plazo pudo vencer) y emite una tarjeta nueva (o explica la inelegibilidad). `confirmed_at` lo fija el **servidor** al recibir `resume`, no el cliente.
6. **Idempotencia de la creación**: `Idempotency-Key = sha256(thread_id + ":" + hash)`; reanudar dos veces (doble clic, reintento de red, reejecución del nodo) nunca duplica la solicitud (la segunda devuelve `replayed`).
7. **Pending interrupt tras recarga**: `GET /chat/threads/{id}/pending-interrupt` lee el estado del checkpointer (`graph.aget_state(config).tasks[*].interrupts`) y devuelve el payload (200) o 204. No requiere que el stream siga abierto.
8. **Un solo interrupt vivo por hilo**: el servicio rechaza `messages`/`resume` concurrentes sobre el mismo hilo con un lock de aplicación (`pg_advisory_xact_lock(hash(thread_id))` en una conexión dedicada durante el turno) → 409 `THREAD_BUSY` para la segunda solicitud.

## 7. Persistencia y resiliencia

- **Checkpointer**: `AsyncPostgresSaver` (`langgraph-checkpoint-postgres`), conexión propia con pool `psycopg_pool.AsyncConnectionPool` (`autocommit=True`, `row_factory=dict_row`, requisito del saver) creada en el `lifespan`; `await saver.setup()` crea sus tablas (se ejecutan **fuera de Alembic**; documentarlo y excluirlas de `alembic check`/autogenerate con `include_object`). `thread_id` = `chat_threads.id` (UUID como str); el cliente puede cerrar el navegador y retomar. Windows: psycopg async requiere `SelectorEventLoop` (ya contemplado en tests y en el comando de uvicorn).
- **Configuración por invocación**: `{"configurable": {"thread_id", "customer_id", "role", "request_id"}, "recursion_limit": 25}`. `customer_id`/`role` salen del `Principal` autenticado del request (no del cuerpo).
- **Límites**: `recursion_limit=25`; `tool_budget=6` por turno; timeout por nodo LLM (`LLM_TIMEOUT_S`) vía `invoke_llm`; `RetryPolicy(max_attempts=3, backoff, retry_on=transitorias)` en nodos con IO; `max_tokens` por llamada.
- **Degradación**: clasificador por palabras clave si falla el LLM 2 veces; RAG sin evidencia → "no lo sé" + `handoff`; vector store caído → aviso + `handoff`; LLM caído por completo (breaker abierto) → respuesta fija recomendando el formulario manual, **sin perder el hilo**; el formulario y toda la REST siguen funcionando.
- **Streaming** (`runtime.py`): `graph.astream(input, config, stream_mode=["messages","updates","custom"])` traducido a SSE: `messages` (tokens del nodo `respond`/`policy_qa`/`explain_ineligible`, no los de clasificación ni de guardrails) → `token`; `updates` con `tool_calls`/resultados → `tool_call`/`tool_result` (con nombre y estado, **sin** argumentos sensibles ni payloads completos); `__interrupt__` → `interrupt` con el payload; fin → `final {message_id, citations, refund_id?}`; excepción → `error {code, message}` con mensaje seguro (`LLM_UNAVAILABLE`, `TOKEN_BUDGET_EXCEEDED`, `INTERNAL`). Cada evento SSE: `event:`, `id:` incremental, `data:` JSON; heartbeat `: ping` cada 15 s.
- **Los eventos custom** (`get_stream_writer`) se usan solo para `tool_call` legibles ("Consultando tus compras…") sin datos.

## 8. API de chat (`api/routes/chat.py`, servicio `ChatService`)

Todas requieren `chat:use` (solo customer: supervisor → 403) y **propiedad del hilo** (ajeno → 404). Base `/api/v1`.

| Ruta | Detalle |
| --- | --- |
| `POST /chat/threads` | crea `chat_threads(customer_id, status=ACTIVE)`; 201 `{thread_id, created_at}` |
| `GET /chat/threads` | hilos propios, cursor (`limit`, `cursor`), con `status` y fecha del último mensaje |
| `GET /chat/threads/{id}/messages` | historial **leído del checkpointer** (`aget_state(...).values["messages"]`) proyectado a `{id, role, content, created_at, citations?}`: solo mensajes `user`/`assistant`; **no** `ToolMessage`s ni mensajes de sistema; si hay interrupt pendiente lo indica (`pending_interrupt: bool`) |
| `POST /chat/threads/{id}/messages` | cuerpo `{content: str (1–2000)}`; respuesta `text/event-stream`. Previos: rate limit **20 mensajes/min** por customer (429 + `Retry-After`; en memoria como en 01), hilo no `NEEDS_HUMAN`/`CLOSED` (409 `THREAD_CLOSED`), sin interrupt pendiente (409 `INTERRUPT_PENDING` con payload), `THREAD_BUSY` (409), presupuesto de tokens del hilo (`CHAT_THREAD_TOKEN_BUDGET`, default 40 000; al excederlo evento `error TOKEN_BUDGET_EXCEEDED` y el hilo se corta con mensaje claro) |
| `POST /chat/threads/{id}/resume` | cuerpo `ResumeValue`; SSE. 409 `INTERRUPT_ALREADY_RESOLVED` si `interrupt_id` no es el pendiente; 404 si no hay interrupt |
| `GET /chat/threads/{id}/pending-interrupt` | 200 con payload / 204 |

Errores previos al stream son problem+json normales (el stream no se abre). Los errores durante el stream se emiten como evento `error` y se cierra. La desconexión del cliente cancela el turno de forma limpia (el estado ya persistido queda consistente; un `interrupt` ya emitido sigue recuperable por `pending-interrupt`). Cada turno registra `tokens` usados (callback de uso de LangChain) en `thread_tokens`.

## 9. Guardrails

**Entrada** (`guardrails/input.py`):
- Longitud máxima 2000 caracteres (truncado + flag).
- PII: emails, teléfonos, números de tarjeta (Luhn) → `[EMAIL]`, `[PHONE]`, `[CARD]` antes de entrar al estado/LLM; el mapeo de valores originales **no** se persiste.
- Injection: reglas (patrones ES/EN: "ignora las instrucciones", "eres ahora", "system prompt", etiquetas `<untrusted_content>`/`</context>` falsas, delimitadores de rol) + clasificador (modelo rápido). Resultado: bloquear (mensaje seguro) o marcar.
- Los textos libres del customer (también `reason_text`) se pasan a los LLM dentro de `<user_input>…</user_input>` con la advertencia de sistema de que es dato.

**Salida** (`guardrails/output.py`, ampliando 02): ver `guard_output`. Reglas deterministas; nunca ids de supervisor, notas internas ni datos de otros usuarios; afirmaciones de política sin cita → no se emiten.

**Privacidad en datos persistidos**: el estado y por tanto los checkpoints solo contienen texto ya enmascarado. (Las trazas externas están fuera de alcance; cuando se añadan en la fase de observabilidad deberán recibir solo estos textos.)

## 10. Frontend: chat del customer (Tailwind)

Ruta `/chat` (customer; el supervisor no la ve). Stack de 01: `@microsoft/fetch-event-source` (POST + `Authorization`), TanStack Query.

- `api/chat.ts` + `useChatStream`: abre el stream, parsea eventos (`token` → concatena en el mensaje del asistente en curso; `tool_call` → chip "Consultando tus compras…" que se resuelve con `tool_result`; `interrupt` → monta la `ConfirmationCard`; `final` → cierra y guarda `citations`; `error` → banner con reintento). Cancela con `AbortController` al desmontar/enviar otro mensaje. Reconexión **no** automática (Last-Event-ID está fuera de alcance): al fallar, el usuario ve "Se interrumpió. Reintentar" y se recarga el historial.
- `ChatPage`: lista de hilos (barra lateral, "Nueva conversación"), `MessageList` (burbujas con Tailwind, markdown seguro sin HTML crudo, citas como chips `doc › sección` con tooltip), `Composer` (Enter envía, Shift+Enter salto, contador 2000, deshabilitado mientras hay stream o interrupt pendiente), indicador "escribiendo".
- **`ConfirmationCard`** (compartida con el modal del formulario de 01): compra/curso, monto, método (original o créditos), razón, fecha límite, citas y tres botones **Confirmar / Editar razón / Cancelar**; cuenta regresiva hasta `expires_at` (vencida → botones deshabilitados y mensaje "Esta confirmación venció, escribe de nuevo para generar una nueva"). Editar abre un textarea validado (20–1000). Al pulsar se llama a `/resume` y se muestra el stream de la continuación. Doble clic bloqueado en cliente y seguro en servidor (409/replayed).
- **Recarga con interrupt pendiente**: al montar el hilo se llama a `GET …/pending-interrupt`; si hay payload se renderiza la tarjeta aunque no haya stream; el `Composer` queda deshabilitado con el texto "Tienes una confirmación pendiente".
- Manejo visible de: 429 (con `Retry-After`), 409 `INTERRUPT_PENDING` (re-muestra la tarjeta), `TOKEN_BUDGET_EXCEEDED` (hilo cerrado, botón "Nueva conversación"), `LLM_UNAVAILABLE` (enlace al formulario manual), `NEEDS_HUMAN` (banner con el canal de soporte).
- Accesibilidad: región `aria-live="polite"` para el stream, foco a la tarjeta al aparecer, botones con etiquetas claras.
- Tests (Vitest + MSW con un `ReadableStream` simulado): streaming concatena tokens; `interrupt` renderiza la tarjeta; recarga recupera la tarjeta; confirmar/cancelar/editar llaman a `/resume` con el `interrupt_id`; tarjeta vencida deshabilita acciones; 429 muestra el aviso.

## 11. Plan de tareas (en orden)

- [ ] **T1** Dependencias (`langgraph`, `langgraph-checkpoint-postgres`, `psycopg-pool`), config (`CHAT_THREAD_TOKEN_BUDGET`, `GRAPH_RECURSION_LIMIT`), `include_object` de Alembic para tablas del checkpointer.
- [ ] **T2** `state.py`, `events.py`, `routing.py` (+ tests de tabla) y `fallback.py`.
- [ ] **T3** Guardrails de entrada/PII y ampliación de salida (+ tests).
- [ ] **T4** Nodos deterministas: `entry_router`, `guard_input`, `check_eligibility`, `create_refund`, `handoff`, `guard_output`, `respond`.
- [ ] **T5** Nodos con LLM: `classify_intent`, `collect_refund_info`, `explain_ineligible`, `policy_qa`, `refund_status`; prompts versionados.
- [ ] **T6** HITL: `prepare_confirmation` + `request_confirmation`, `cancel_refund_flow`, expiración y `edit`.
- [ ] **T7** `builder.py` + `checkpointer.py` + `runtime.py` (astream → SSE, reanudación, límites, presupuesto).
- [ ] **T8** `ChatService` + `routes/chat.py` (+ rate limit, locks, `pending-interrupt`) + wiring en `lifespan`.
- [ ] **T9** Tests de trayectorias, interrupt/checkpoint, contrato SSE, API de chat y seguridad.
- [ ] **T10** Evals del agente (30 guiones) + runner.
- [ ] **T11** Frontend de chat + tarjeta HITL + tests.
- [ ] **T12** README/CLAUDE.md: cómo correr (API, worker, ingesta, chat) y guion de demo de 3 minutos.

## 12. Criterios de aceptación y tests

**Fakes**: `tests/ai/fakes.py` (de 02) con `ScriptedChatModel` por nodo (clasificador, extractor, redactor) y `FakeRetriever`; checkpointer real sobre Postgres de test (`AsyncPostgresSaver`) para los tests de persistencia, `MemorySaver` para los de trayectoria rápidos.

| Área | Criterio | Test |
| --- | --- | --- |
| Estado | `SupportState` no contiene `customer_id`, `role`, `request_id` ni objetos no serializables; el checkpoint se serializa y restaura | `test_state.py`, `test_checkpoint.py` |
| Ruteo | Cada arista condicional devuelve el destino esperado para todas sus entradas (tabla de verdad); `awaiting` salta `classify_intent` | `test_routing.py` |
| Trayectoria feliz | "quiero reembolso del curso X" → compra resuelta → razón → elegible → **interrupt** → `resume confirm` → solicitud `PENDING_APPROVAL` creada con `channel=CHAT`, `chat_thread_id`, `actor_type=AGENT`, `confirmation_hash` | `test_trajectories.py` |
| Inelegible | fuera de plazo → `explain_ineligible` con cita de "Ventana de 30 días", **sin** interrupt y **sin** solicitud; `eligibility_checks` guardado | idem |
| Cancela en HITL | `resume cancel` → no se crea nada, respuesta explícita | idem |
| Edita razón | `resume edit` con razón nueva → vuelve a `collect`/`check` y emite una tarjeta nueva con hash distinto; el hash viejo no sirve | idem |
| Interrupt vencido | con `Clock` adelantado más allá de `expires_at`: no se crea nada, se reevalúa elegibilidad y se emite tarjeta nueva (o explicación si ya venció la ventana) | idem |
| Falta razón | sin razón válida el grafo pregunta (`awaiting="reason"`) y **nunca** emite el interrupt | idem |
| Prompt injection | 100 % del set adversarial (mínimo 15 mensajes de usuario y 5 chunks envenenados) → 0 filas nuevas en `refund_requests`/`refund_events` y ninguna tool no autorizada invocada; el grafo no tiene camino a `create_refund` sin `interrupt` resuelto (se verifica también estructuralmente recorriendo `graph.get_graph()`: toda arista hacia `create_refund` sale de `request_confirmation`) | `test_trajectories.py`, `test_graph_structure.py` |
| Allowlist | Las tools disponibles por nodo coinciden con `TOOLSETS`; ningún nodo con LLM puede llamar tools 6/9 | `test_graph_structure.py` |
| Doble reanudación | `resume` dos veces con el mismo `interrupt_id` → 2.º devuelve 409; si el nodo se reejecuta, `Idempotency-Key` evita duplicados (1 sola fila) | `test_interrupt.py` |
| Persistencia | Crear hilo → mensaje → cortar el stream en `interrupt` → **nueva instancia del grafo/proceso simulado** → `pending-interrupt` devuelve la tarjeta y `resume` completa la creación | `test_checkpoint.py` |
| Concurrencia | Dos `messages` simultáneos al mismo hilo → uno `THREAD_BUSY`; mensaje libre con interrupt pendiente → 409 `INTERRUPT_PENDING` con payload | `test_chat_api.py` |
| Autorización | Customer B → 404 en hilos/mensajes/resume/pending-interrupt de A; supervisor → 403 en `/chat/*` | idem |
| SSE | El stream emite la secuencia `tool_call` → `tool_result` → `token`* → `final`; `interrupt` lleva el payload completo; los errores salen como `error` y cierran; no aparecen argumentos sensibles ni `ToolMessage` crudos | `test_sse_contract.py` |
| Límites | 21.º mensaje en un minuto → 429 con `Retry-After`; presupuesto de tokens excedido → `error TOKEN_BUDGET_EXCEEDED` y mensaje claro; `recursion_limit` y `tool_budget` evitan bucles (LLM guionado que pide tools indefinidamente termina con mensaje seguro) | `test_chat_api.py`, `test_trajectories.py` |
| Degradación | LLM caído → clasificador por palabras clave (con flag), luego mensaje que recomienda el formulario; vector store caído → aviso + oferta de handoff; en ambos casos `POST /refunds` (formulario) sigue funcionando | idem |
| Privacidad | Email/teléfono/tarjeta en el mensaje del usuario no aparecen en el estado checkpointed ni en los prompts armados (se inspeccionan); notas internas y `decided_by` no aparecen en ningún prompt ni mensaje | `test_guardrails.py` |
| Salida | Un borrador con email ajeno, id de supervisor, texto de nota o cifra de política sin cita es bloqueado/sustituido | idem |
| Evals del agente | 30 guiones (`evals/agent/scripts.jsonl`: intención esperada, trayectoria de nodos, tools permitidas, resultado) sobre LLM real en modo `full` y sobre fakes en CI: intención correcta ≥ 95 %, **cero** llamadas a tools no autorizadas, finalización de tarea ≥ 90 % | `evals/agent/runner.py` (`make evals-agent`) |
| Frontend | Tests de §10; `npm run lint` y `build` verdes; la tarjeta sobrevive a recarga | Vitest |
| Calidad | `ruff`, `mypy` (strict en `ai/graph/state.py`, `routing.py`, `guardrails/`); todos los tests offline | CI local |
| Demo (3 min) | `seed` → ingesta de políticas → login `ann` → chat: duda de política con citas → "quiero reembolsar X" → tarjeta → recargar la página → confirmar → login supervisor → aprobar → worker → estado `COMPLETED` en el chat | manual (guion en README) |
