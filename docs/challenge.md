# Challenge Senior: Customer Support con Refunds (LangGraph + FastAPI + RAG)

Nov 14, 2026 · @Leo Valeriano

El cliente pide reembolsos por chat (agente LangGraph) o por formulario manual; solo el supervisor aprueba, desde un dashboard sin agente. La plataforma ficticia ("Learnly", estilo Udemy) usa la política de reembolsos de Udemy como base de conocimiento.

## 1. Visión y decisiones de diseño

El núcleo senior del challenge es separar lo que decide el código (elegibilidad, permisos, estados) de lo que explica el LLM (políticas, conversación), con un único punto de entrada de dominio para chat y formulario.

| # | Decisión | Elección | Por qué |
| --- | --- | --- | --- |
| D1 | Quién decide la elegibilidad | `RefundPolicyEngine` determinista en Python; el LLM nunca decide | Un LLM puede alucinar un plazo; la decisión debe ser testeable y auditable |
| D2 | Rol del RAG | Responde dudas de política y justifica rechazos con citas | Los parámetros ejecutables (30 días, tope de consumo) salen del front-matter YAML versionado de los mismos documentos: una sola fuente de verdad |
| D3 | Punto de entrada | `RefundService.create_request()` lo llaman la tool del agente y el endpoint manual | Las reglas no se duplican entre canales |
| D4 | Human-in-the-loop | Confirmación explícita del cliente antes de crear la solicitud: `interrupt()` en el chat, modal con `confirmation_token` en el formulario | Evita reembolsos creados por malentendido o por prompt injection |
| D5 | Aprobación | Solo el supervisor, vía API del dashboard; el agente no tiene ninguna tool de aprobación | Separación de funciones |
| D6 | Solicitud inelegible | No se crea; la API responde 422 con `reason_codes`, y se guarda un registro en `eligibility_checks` | Pediste que no se pueda ejecutar; la auditoría se conserva sin ensuciar la cola del supervisor |
| D7 | Ejecución del pago | Outbox + worker contra un gateway simulado, con idempotencia | Aprobar y pagar son operaciones distintas y pueden fallar por separado |
| D8 | Versionado | Cada solicitud guarda `policy_version` y `policy_snapshot` | Se puede reconstruir con qué reglas se evaluó |

**Stack propuesto:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 + Alembic, PostgreSQL con pgvector, Redis (rate limit y locks), LangGraph con checkpointer Postgres, LangChain para retriever, splitters y embeddings, Langfuse o LangSmith para trazas, pytest. Frontend React/Next.js con dos vistas: widget de chat con tarjeta de confirmación y dashboard del supervisor.

**Fuera de alcance:** pagos reales, catálogo de cursos completo, reproductor de video, soporte multi-idioma de las políticas.

## 2. Roles y matriz de permisos

Hay dos roles humanos (`customer`, `supervisor`) y un actor de sistema, el agente. El agente nunca tiene identidad propia: actúa con la identidad del customer autenticado y con un subconjunto de sus permisos. El supervisor no tiene agente ni acceso al chat.

| Capacidad | Customer | Supervisor | Agente (en nombre del customer) |
| --- | --- | --- | --- |
| Ver sus compras e inscripciones | Sí, solo las propias | Solo las de la solicitud que revisa | Sí, solo las propias |
| Consultar políticas (RAG) | Sí | Sí, visor de políticas en el dashboard (sin chat) | Sí |
| Simular elegibilidad de una compra | Sí | No | Sí |
| Crear solicitud de reembolso | Sí | **No** | Sí, solo tras confirmación humana (HITL) |
| Ver sus solicitudes | Sí, las propias | Sí, todas | Sí, las propias |
| Cancelar solicitud propia (solo en `PENDING_APPROVAL`) | Sí | No | Sí, solo tras confirmación humana |
| Aprobar o rechazar | **No** | Sí | **No** (la tool no existe) |
| Escribir nota interna | No | Sí | No |
| Leer notas internas | No | Sí | **No** (nunca entran al contexto del LLM) |
| Ver timeline de eventos de una solicitud | Sí, sin notas internas | Sí, completo | Sí, sin notas internas |
| Reintentar un pago fallido | No | Sí | No |
| Ver datos personales de otros customers | No | Solo los mínimos de la solicitud (nombre, email enmascarado) | No |
| Ingestar o reindexar políticas | No | No | No (job de ops con service token) |

### Reglas de permiso del agente

1. **Identidad inyectada, no solicitada.** `customer_id` y `role` viajan en `RunnableConfig["configurable"]` desde el JWT; ninguna tool los recibe como argumento del LLM.
2. **Allowlist de tools.** El grafo solo registra las tools de la sección 7; no hay tool de aprobación, ni de pago, ni de lectura de notas internas.
3. **Toda tool mutante pasa por HITL.** `create_refund_request` y `cancel_refund_request` solo se ejecutan después de que el cliente confirma en la UI; la confirmación queda ligada a un hash del payload exacto.
4. **El agente no puede cambiar lo ya enviado.** Una vez `PENDING_APPROVAL`, solo puede cancelar; no editar razón ni compra.
5. **Salida filtrada.** Un guardrail de salida bloquea ids de supervisor, notas internas y datos de otros usuarios.
6. **Contenido recuperado es dato, no instrucción.** Los chunks del RAG y los textos del cliente se tratan como no confiables (defensa contra prompt injection).

### Reglas de permiso del supervisor

1. No puede aprobar ni rechazar una solicitud donde `customer_id == supervisor_id` (conflicto de intereses).
2. Rechazar exige `decision_note` de al menos 10 caracteres; aprobar puede llevarla opcional.
3. La decisión es inmutable: una solicitud `APPROVED` o `REJECTED` no se re-decide. La única acción posterior es reintentar un pago `FAILED`.
4. Dos supervisores no pueden decidir la misma solicitud: control de concurrencia optimista con `version` (409 si cambió).

## 3. Modelo de datos y campos

La entidad central es `refund_requests`; todo lo demás la soporta, la audita o alimenta la elegibilidad.

### `refund_requests`

| Campo | Tipo | Notas |
| --- | --- | --- |
| `id` | UUID | PK |
| `customer_id` | UUID FK | Quien solicita; único rol autorizado a crear |
| `purchase_id` | UUID FK | Índice único parcial: una sola solicitud activa por compra |
| `status` | enum | `PENDING_APPROVAL`, `APPROVED`, `REJECTED`, `CANCELLED`, `PROCESSING`, `COMPLETED`, `FAILED` |
| `reason_category` | enum | `NOT_AS_DESCRIBED`, `ACCIDENTAL_PURCHASE`, `TECHNICAL_ISSUES`, `CONTENT_OUTDATED`, `FOUND_ALTERNATIVE`, `NO_LONGER_NEEDED`, `OTHER` |
| `reason_text` | text | Obligatorio, 20 a 1000 caracteres |
| `refund_method` | enum | `ORIGINAL_PAYMENT` o `PLATFORM_CREDITS`; lo fija el engine, no el cliente |
| `amount`, `currency` | numeric, char(3) | Copiados de la compra al crear |
| `channel` | enum | `CHAT` o `MANUAL_FORM` |
| `chat_thread_id` | UUID nullable | Presente si el canal es `CHAT` |
| `eligibility_snapshot` | JSONB | Salida completa del engine: reglas evaluadas, `days_since_purchase`, `progress_pct` |
| `policy_version` | text | Versión de la política usada |
| `confirmed_at`, `confirmation_hash` | timestamptz, text | Evidencia del HITL |
| `decided_by`, `decided_at`, `decision_note` | UUID, timestamptz, text | Se llenan al aprobar o rechazar |
| `idempotency_key` | text | Único por `customer_id` |
| `version` | int | Bloqueo optimista |
| `created_at`, `updated_at` | timestamptz |  |

### Entidades de soporte

| Entidad | Campos clave | Para qué sirve |
| --- | --- | --- |
| `users` | `id`, `email`, `name`, `role` (`customer` o `supervisor`), `status` | Identidad y RBAC |
| `courses` | `id`, `title`, `instructor_name`, `price`, `currency` | Catálogo mínimo |
| `purchases` | `id`, `customer_id`, `course_id`, `amount`, `currency`, `payment_method` (`CARD`, `PAYPAL`, `APPLE_IAP`, `GOOGLE_IAP`, `CREDITS`), `purchase_type` (`INDIVIDUAL`, `GIFT`, `SUBSCRIPTION`, `THIRD_PARTY`), `purchased_at`, `status` (`ACTIVE`, `REFUNDED`) | Fuente de verdad de la compra |
| `course_progress` | `purchase_id`, `progress_pct`, `lectures_completed`, `last_accessed_at`, `certificate_issued_at` | Insumo del límite de consumo |
| `eligibility_checks` | `id`, `customer_id`, `purchase_id`, `source`, `eligible`, `reason_codes[]`, `policy_version`, `details` | Auditoría de consultas e intentos rechazados |
| `refund_events` | `id`, `refund_id`, `type`, `actor_type` (`CUSTOMER`, `SUPERVISOR`, `AGENT`, `SYSTEM`), `actor_id`, `payload`, `created_at` | Log append-only; alimenta el timeline |
| `refund_internal_notes` | `id`, `refund_id`, `supervisor_id`, `body`, `created_at` | Notas visibles solo al supervisor |
| `refund_payouts` | `id`, `refund_id`, `method`, `amount`, `provider_ref`, `status`, `attempts`, `last_error`, `idempotency_key` | Ejecución del pago, separada de la aprobación |
| `credit_ledger` | `id`, `customer_id`, `delta`, `refund_id`, `created_at` | Libro append-only de créditos; el saldo es la suma |
| `outbox_events` | `id`, `topic`, `payload`, `status`, `available_at` | Entrega confiable al worker y a notificaciones |
| `chat_threads` | `id`, `customer_id`, `status`, `created_at` | Los checkpoints los gestiona el checkpointer de LangGraph |
| `policy_documents` | `id`, `doc_key`, `version`, `effective_date`, `checksum`, `status` (`ACTIVE`, `SUPERSEDED`), `params` (JSONB del front-matter) | Políticas versionadas |
| `policy_chunks` | `id`, `document_id`, `section_path`, `heading_level`, `content`, `embedding` (vector), `tsv`, `metadata` | Índice del RAG |
|  |  |  |

### Códigos de inelegibilidad (`reason_codes`)

`OUTSIDE_REFUND_WINDOW`, `ALREADY_REFUNDED`, `ACTIVE_REFUND_EXISTS`, `PREVIOUSLY_REJECTED`, `CONSUMPTION_LIMIT_EXCEEDED`, `CERTIFICATE_ISSUED`, `SUBSCRIPTION_NOT_ELIGIBLE`, `THIRD_PARTY_PURCHASE`, `REFUND_ABUSE_LIMIT`, `PURCHASE_NOT_FOUND` (también cuando la compra es de otro usuario, para no filtrar existencia).

## 4. Business rules y máquina de estados

Son 24 reglas en cuatro grupos. Los umbrales no van hardcodeados: se cargan del front-matter de la política activa (`refund_window_days: 30`, `max_progress_pct: 30`, `abuse_block_12m: 5`, `abuse_flag_12m: 3`, `confirmation_ttl_min: 10`, `sla_hours: 48`). Los valores distintos de los 30 días son parámetros del challenge, no cifras publicadas por Udemy.

### Elegibilidad (las evalúa `RefundPolicyEngine`, siempre en código)

| ID | Regla | Resultado si falla |
| --- | --- | --- |
| BR-01 | La compra pertenece al usuario autenticado | `PURCHASE_NOT_FOUND` (mismo código si es ajena, para no filtrar existencia) |
| BR-02 | `now - purchased_at <= 30 días`, límite inclusivo, en UTC y con reloj inyectable para tests | `OUTSIDE_REFUND_WINDOW` |
| BR-03 | `purchase.status == ACTIVE` | `ALREADY_REFUNDED` |
| BR-04 | No existe otra solicitud en `PENDING_APPROVAL`, `APPROVED` o `PROCESSING` para la compra | `ACTIVE_REFUND_EXISTS` |
| BR-05 | Una compra con solicitud `REJECTED` no se puede volver a solicitar; tras `CANCELLED` sí, dentro de la ventana | `PREVIOUSLY_REJECTED` |
| BR-06 | `progress_pct <= 30` | `CONSUMPTION_LIMIT_EXCEEDED` |
| BR-07 | `certificate_issued_at` es nulo | `CERTIFICATE_ISSUED` |
| BR-08 | `purchase_type` no es `SUBSCRIPTION` ni `THIRD_PARTY` | `SUBSCRIPTION_NOT_ELIGIBLE` / `THIRD_PARTY_PURCHASE` |
| BR-09 | Compras `GIFT`, `CREDITS`, `APPLE_IAP` y `GOOGLE_IAP` se reembolsan solo como `PLATFORM_CREDITS`; `CARD` y `PAYPAL` al medio original | Fija `refund_method`; no bloquea |
| BR-10 | Menos de 5 reembolsos completados en 12 meses; con 3 o más se marca `HIGH_REFUND_FREQUENCY` para el supervisor | `REFUND_ABUSE_LIMIT` al llegar a 5; la marca no bloquea |

### Creación de la solicitud

| ID | Regla | Resultado si falla |
| --- | --- | --- |
| BR-11 | Solo el rol `customer` crea solicitudes | 403 |
| BR-12 | Razón obligatoria: `reason_category` del enum y `reason_text` de 20 a 1000 caracteres con al menos 3 palabras distintas. El agente no llega al paso de confirmación sin ella | 422 `REASON_REQUIRED` |
| BR-13 | Confirmación humana obligatoria. En chat: `interrupt()`. En formulario: `confirmation_token` con TTL de 10 min, ligado al hash de `{purchase_id, reason_category, reason_text, amount, refund_method}`; si el payload cambia, el token no vale | 422 `CONFIRMATION_REQUIRED` / `CONFIRMATION_INVALID` |
| BR-14 | La elegibilidad se reevalúa dentro de la transacción de creación (evita que el plazo venza entre previsualizar y confirmar) | 422 con `reason_codes` |
| BR-15 | `Idempotency-Key` obligatorio en `POST /refunds`. Misma clave y mismo cuerpo devuelven la respuesta original; misma clave con cuerpo distinto es error | 409 `IDEMPOTENCY_CONFLICT` |
| BR-16 | La ventana de 30 días se mide hasta `created_at` de la solicitud, no hasta la decisión: si el supervisor aprueba el día 35, sigue siendo válida | n/a |

### Aprobación (solo supervisor, solo dashboard)

| ID | Regla | Resultado si falla |
| --- | --- | --- |
| BR-17 | Solo el rol `supervisor` decide, y solo desde `PENDING_APPROVAL` | 403 / 409 |
| BR-18 | Decidir exige `If-Match: <version>`; si otro supervisor decidió antes | 409 `VERSION_CONFLICT` |
| BR-19 | Rechazar exige `decision_note` de al menos 10 caracteres | 422 |
| BR-20 | No se decide una solicitud propia (`customer_id == supervisor_id`) | 403 `SELF_APPROVAL_FORBIDDEN` |
| BR-21 | Pasadas 48 h en `PENDING_APPROVAL` se marca `sla_breached` y se resalta en el dashboard; nunca hay auto-aprobación | n/a |

### Ejecución y cierre

| ID | Regla | Resultado |
| --- | --- | --- |
| BR-22 | Al aprobar, se escribe `outbox_events` en la misma transacción; un worker ejecuta el pago con `idempotency_key = refund_id` y 3 reintentos con backoff exponencial | `PROCESSING`, luego `COMPLETED` o `FAILED` |
| BR-23 | En `COMPLETED`: `purchase.status = REFUNDED`, se revoca el acceso al curso y, si es `PLATFORM_CREDITS`, se inserta en `credit_ledger` | Atómico por pago |
| BR-24 | El cliente solo cancela en `PENDING_APPROVAL`; cada transición escribe un `refund_event` y notifica al cliente | `CANCELLED` |

### Máquina de estados

&#91;embedded content: máquina de estados del reembolso · 7 estados\]

Una solicitud nace en `PENDING_APPROVAL`; desde ahí solo el supervisor aprueba o rechaza y solo el cliente cancela. Los estados posteriores a `APPROVED` los avanza el worker, y un pago fallido vuelve a `PROCESSING` solo si el supervisor lo reintenta.

## 5. Endpoints de la API

Base `/api/v1`, JWT Bearer con claims `sub` y `role`. El rol se valida con una dependencia de FastAPI (`require_role`), no dentro de cada handler. Los errores usan `application/problem+json` con `code`, `reason_codes` y `trace_id`.

### Compras y elegibilidad (customer)

| Método y ruta | Descripción | Respuestas clave |
| --- | --- | --- |
| `GET /me` | Perfil, rol y saldo de créditos | 200 |
| `GET /purchases` | Compras propias, filtros `status`, `within_window`, paginación por cursor | 200 |
| `GET /purchases/{id}` | Detalle con progreso y fecha límite de reembolso | 200, 404 |
| `GET /purchases/{id}/refund-eligibility` | Resultado del engine con `reason_codes`; guarda un `eligibility_checks` | 200 |

### Reembolsos (customer)

| Método y ruta | Descripción | Respuestas clave |
| --- | --- | --- |
| `POST /refunds/preview` | Valida elegibilidad y razón, devuelve monto, método, citas de política y `confirmation_token` | 200 (`eligible: true/false`), 422 |
| `POST /refunds` | Crea la solicitud. Exige `Idempotency-Key` y `confirmation_token` | 201, 403, 409, 422 |
| `GET /refunds` | Solicitudes propias, filtro `status` | 200 |
| `GET /refunds/{id}` | Detalle sin notas internas | 200, 404 |
| `GET /refunds/{id}/events` | Timeline público del reembolso | 200 |
| `POST /refunds/{id}/cancel` | Cancela si está en `PENDING_APPROVAL`; exige `If-Match` | 200, 409 |

### Chat con el agente (customer)

| Método y ruta | Descripción | Respuestas clave |
| --- | --- | --- |
| `POST /chat/threads` | Crea un hilo (`thread_id` = id del grafo) | 201 |
| `GET /chat/threads` | Hilos propios | 200 |
| `GET /chat/threads/{id}/messages` | Historial leyendo el estado del checkpointer | 200 |
| `POST /chat/threads/{id}/messages` | Envía un mensaje; responde por SSE con eventos `token`, `tool_call`, `tool_result`, `interrupt`, `final`, `error` | 200 (stream), 429 |
| `POST /chat/threads/{id}/resume` | Reanuda tras el HITL con `{interrupt_id, decision}`; también SSE | 200 (stream), 409 si el interrupt ya se resolvió |
| `GET /chat/threads/{id}/pending-interrupt` | Recupera la tarjeta de confirmación si el cliente recarga la página | 200, 204 |

### Dashboard del supervisor

| Método y ruta | Descripción | Respuestas clave |
| --- | --- | --- |
| `GET /supervisor/refunds` | Cola con filtros `status`, `sla_breached`, `high_risk`, `channel`, `reason_category`, rango de fechas, búsqueda por cliente, orden y cursor | 200 |
| `GET /supervisor/refunds/{id}` | Detalle completo: razón, `eligibility_snapshot`, compra, progreso, historial del cliente, flags, citas de política, notas | 200 |
| `POST /supervisor/refunds/{id}/approve` | Aprueba; `If-Match`, `decision_note` opcional | 200, 403, 409 |
| `POST /supervisor/refunds/{id}/reject` | Rechaza; `If-Match`, `decision_note` obligatoria | 200, 403, 409, 422 |
| `POST /supervisor/refunds/{id}/retry-payout` | Reintenta un pago en `FAILED` | 202, 409 |
| `GET /supervisor/refunds/{id}/notes` y `POST` | Notas internas | 200, 201 |
| `GET /supervisor/refunds/{id}/events` | Timeline completo | 200 |
| `GET /supervisor/stats` | Pendientes, SLA vencido, tiempo medio de decisión, tasa de aprobación, top razones | 200 |
| `GET /supervisor/refunds/stream` | SSE con solicitudes nuevas y cambios de estado | 200 (stream) |

### Políticas y operación

| Método y ruta | Rol | Descripción |
| --- | --- | --- |
| `GET /policies` y `GET /policies/{doc_key}` | customer, supervisor | Documentos activos con versión y fecha de vigencia (visor, sin LLM) |
| `GET /healthz`, `GET /readyz`, `GET /metrics` | público / interno | Salud, readiness (DB, Redis, vector store) y Prometheus |

La ingesta de políticas no es endpoint: es un comando `python -m app.rag.ingest` que corre en CI o como job de ops.

### Contratos clave

```json
// POST /refunds/preview  → 200
{
  "eligible": true,
  "reason_codes": [],
  "purchase_id": "b1f0…",
  "amount": 49.99, "currency": "USD",
  "refund_method": "ORIGINAL_PAYMENT",
  "days_since_purchase": 12, "refund_window_days": 30,
  "progress_pct": 8,
  "policy_version": "2026-10-01",
  "citations": [{"doc_key": "refund-eligibility", "section_path": "Elegibilidad > Ventana de 30 días"}],
  "confirmation_token": "eyJ…", "expires_at": "2026-11-14T21:40:00Z"
}
```

```json
// POST /refunds  (headers: Idempotency-Key, Authorization)  → 201
{ "purchase_id": "b1f0…", "reason_category": "NOT_AS_DESCRIBED",
  "reason_text": "El curso promete proyectos reales y solo hay teoría básica.",
  "confirmation_token": "eyJ…" }
```

```json
// POST /refunds  → 422 (problem+json)
{ "type": "/problems/refund-not-eligible", "code": "REFUND_NOT_ELIGIBLE",
  "reason_codes": ["OUTSIDE_REFUND_WINDOW"], "detail": "Han pasado 41 días; el límite es 30.",
  "trace_id": "9c1e…" }
```

```json
// POST /supervisor/refunds/{id}/reject  (headers: If-Match: 3)
{ "decision_note": "El progreso real supera el límite: 62% según el log de lecciones." }
```

## 6. Arquitectura del grafo LangGraph

El grafo es un híbrido: el camino que mueve dinero (recolectar datos, elegibilidad, confirmación, creación) es un workflow explícito con nodos deterministas, y el LLM solo interviene donde hay lenguaje natural (clasificar intención, extraer la razón, redactar, responder dudas de política). Los caminos de solo lectura (políticas, estado) pueden usar un sub-agente ReAct acotado. Existe un solo grafo, el del customer; el supervisor no tiene ninguno.

### Flujo del grafo

&#91;embedded content: grafo LangGraph del chat · 14 nodos, 2 con HITL\]

El clasificador abre un abanico de intenciones; solo la rama de reembolso llega a `create_refund`, y lo hace después de que `request_confirmation` recibe la decisión del cliente. Todas las ramas convergen en `guard_output` antes de responder.

### Estado (`SupportState`)

| Campo | Tipo | Uso |
| --- | --- | --- |
| `messages` | `list[AnyMessage]` con reducer `add_messages` | Conversación |
| `intent` | enum: `policy_question`, `refund_request`, `refund_status`, `cancel_refund`, `out_of_scope`, `human_handoff` | Ruteo |
| `awaiting` | `None`, `purchase`, `reason` | Si el turno anterior terminó con una pregunta, el siguiente mensaje entra directo a `collect_refund_info` sin reclasificar |
| `selected_purchase_id` | UUID o nulo | Compra elegida y verificada contra el usuario |
| `reason_category`, `reason_text` | enum, texto | Razón extraída del cliente |
| `eligibility` | `EligibilityResult` | Salida del engine, con `reason_codes` y citas |
| `pending_confirmation` | `{hash, expires_at}` | Ata la confirmación al payload exacto |
| `refund_id` | UUID o nulo | Resultado de la creación |
| `citations` | `list[Citation]` | Fuentes del RAG para la respuesta |
| `guardrail_flags` | `list[str]` | Resultado de los guardrails |
| `tool_budget`, `retries` | int | Límites por turno |

`customer_id`, `role` y `request_id` **no** van en el estado: llegan por `config["configurable"]` y las tools los leen de ahí.

### Nodos

| Nodo | Tipo | Qué hace |
| --- | --- | --- |
| `entry_router` | determinista | Si `awaiting` está definido va a `collect_refund_info`; si no, a `guard_input` |
| `guard_input` | determinista + clasificador | Enmascara PII, detecta prompt injection, limita longitud; si bloquea, va a `respond` con un mensaje seguro |
| `classify_intent` | LLM, salida estructurada | Devuelve `intent` y entidades (nombre del curso) con Pydantic, temperatura 0 |
| `policy_qa` | subgrafo RAG | Reescribe la consulta, recupera, filtra por relevancia, genera con citas y verifica el fundamento (sección 8) |
| `collect_refund_info` | LLM + tools de lectura | Resuelve la compra (pregunta si hay varias candidatas) y extrae la razón. Si falta algo, fija `awaiting` y termina el turno con la pregunta |
| `check_eligibility` | determinista | Llama a `check_refund_eligibility`; ramifica a elegible o no |
| `explain_ineligible` | LLM + RAG | Explica los `reason_codes` citando la política y ofrece alternativas válidas; no crea nada |
| `request_confirmation` | **HITL** | Ejecuta `interrupt(payload)` y espera la decisión del cliente |
| `create_refund` | determinista | Llama a `create_refund_request` con el hash de confirmación; maneja 422 y 409 |
| `refund_status` | sub-agente de lectura | Consulta solicitudes propias y resume el estado y el timeline |
| `cancel_refund_flow` | determinista + HITL | Identifica la solicitud, pide confirmación y llama a `cancel_refund_request` |
| `handoff` | determinista | Marca el hilo como `needs_human` y responde con el canal de soporte |
| `guard_output` y `respond` | determinista + LLM | Redacta la respuesta final, verifica que no filtre notas internas ni datos ajenos y que las afirmaciones de política tengan cita |

### Ruteo, interrupt y reanudación

1. **Aristas condicionales** salen de `guard_input` (bloqueado o no), `classify_intent` (seis intenciones), `collect_refund_info` (falta dato o completo), `check_eligibility` (elegible o no) y `request_confirmation` (confirmar, cancelar o editar).
2. **El `interrupt()` es lo primero que ejecuta su nodo.** Al reanudar, LangGraph reejecuta el nodo desde el principio; cualquier efecto anterior al interrupt se repetiría.
3. **Payload del interrupt:** `{kind: "refund_confirmation", purchase, amount, currency, refund_method, reason, window_deadline, policy_citations, hash, expires_at}`. El frontend lo pinta como una tarjeta con tres acciones.
4. **Valor de reanudación:** `{interrupt_id, decision: "confirm" | "cancel" | "edit", edited_reason?}`, validado con Pydantic. `edit` vuelve a `collect_refund_info`.
5. **Mensaje libre con un interrupt pendiente:** la API responde 409 `INTERRUPT_PENDING` con el payload; la tarjeta no desaparece sola.
6. **Interrupt vencido:** si la confirmación llega después de `expires_at`, se vuelve a `check_eligibility` y se emite una tarjeta nueva.
7. **Idempotencia de la creación:** `Idempotency-Key = sha256(thread_id + hash)`, así un reintento del grafo nunca duplica la solicitud.

### Persistencia y resiliencia

- **Checkpointer:** `AsyncPostgresSaver`; `thread_id` = `chat_threads.id`, de modo que el cliente puede cerrar el navegador y retomar.
- **Streaming:** `astream` con `stream_mode=["messages", "updates", "custom"]`, traducido a los eventos SSE de la sección 5.
- **Límites:** `recursion_limit=25`, `tool_budget` de 6 por turno, timeout por nodo de LLM y `RetryPolicy` con backoff en errores transitorios.
- **Degradación:** si el LLM de clasificación falla dos veces, un clasificador por palabras clave toma el relevo; si el RAG no encuentra evidencia suficiente, el agente dice que no lo sabe y ofrece `handoff` en lugar de inventar.

## 7. Lista de tools del agente

Hay diez tools. Las de lectura se enlazan al LLM con `bind_tools`; las dos que escriben **no se enlazan a ningún modelo**: las invoca un nodo determinista después del HITL, con argumentos tomados del estado y no de lo que genere el LLM. Así un prompt injection no puede disparar un reembolso.

| # | Tool | Tipo | Argumentos del LLM | Inyectado por el runtime | Quién la invoca | Guardrails |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | `search_refund_policy` | Lectura | `query`, `top_k` (máx. 6) | `policy_version` activa | `policy_qa`, `explain_ineligible` | Devuelve chunks con cita; contenido marcado como no confiable |
| 2 | `list_my_purchases` | Lectura | `status?`, `within_refund_window?`, `course_query?` | `customer_id` | `collect_refund_info` | Proyección mínima (id, curso, fecha, monto, días restantes); máx. 20 |
| 3 | `get_purchase_details` | Lectura | `purchase_id` | `customer_id` | `collect_refund_info` | Verifica propiedad; ajena responde `PURCHASE_NOT_FOUND` |
| 4 | `check_refund_eligibility` | Lectura (escribe `eligibility_checks`) | `purchase_id` | `customer_id` | `check_eligibility` | Salida del engine; el LLM solo la narra |
| 5 | `prepare_refund_confirmation` | Lectura | `purchase_id`, `reason_category`, `reason_text` | `customer_id` | `request_confirmation` | Valida BR-12; devuelve payload y hash; el token no entra al contexto del LLM |
| 6 | `create_refund_request` | **Escritura** | ninguno: viene del estado | `customer_id`, `confirmation_proof`, `Idempotency-Key` | `create_refund`, solo tras `interrupt` | Reevalúa BR-01 a BR-10 en transacción; registra `actor_type=AGENT` |
| 7 | `list_my_refunds` | Lectura | `status?` | `customer_id` | `refund_status` | Sin notas internas |
| 8 | `get_refund_status` | Lectura | `refund_id` | `customer_id` | `refund_status` | Devuelve estado y timeline público |
| 9 | `cancel_refund_request` | **Escritura** | ninguno: viene del estado | `customer_id`, `confirmation_proof` | `cancel_refund_flow`, solo tras `interrupt` | Solo en `PENDING_APPROVAL`; `If-Match` con `version` |
| 10 | `escalate_to_human` | Escritura de bajo riesgo | `summary`, `category` | `customer_id`, `thread_id` | `handoff` | Marca `needs_human`; no toca reembolsos |

**Tools que no existen, a propósito:** aprobar o rechazar, leer notas internas, ejecutar o reintentar pagos, emitir créditos, buscar usuarios, y cualquier tool que reciba `customer_id` como argumento.

### Contrato común

- Las tools son adaptadores finos sobre la misma capa de servicios que usa REST (`PurchaseService`, `RefundService`, `PolicyRetriever`), con un objeto `Principal(user_id, role, acting_via="agent")`. No se llaman por HTTP a sí mismas.
- Respuesta uniforme: `ToolResult{ok, data, error{code, message, retryable}}`. Los errores de dominio vuelven como datos para que el LLM los explique; los errores de infraestructura reintentan o escalan.
- Esquemas Pydantic estrictos (`extra="forbid"`), descripciones escritas para el modelo y un test que verifica que ninguna tool enlazada al LLM acepte `customer_id` ni `role`.
- Cada escritura deja un `refund_event` con `actor_type=AGENT`, `acting_for=customer_id` y el `trace_id` del run.

```python
class EligibilityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eligible: bool
    reason_codes: list[ReasonCode] = []
    warnings: list[Warning] = []  # p. ej. HIGH_REFUND_FREQUENCY (solo supervisor)
    days_since_purchase: int
    refund_window_days: int
    progress_pct: int
    refund_method: RefundMethod | None
    amount: Decimal | None
    policy_version: str
    checked_at: datetime
```

## 8. Arquitectura del RAG

El corpus es pequeño y de alto riesgo: una respuesta equivocada sobre el plazo cuesta dinero. Por eso el diseño prioriza precisión, citas verificables y capacidad de abstenerse, no volumen. El RAG explica y justifica; nunca decide la elegibilidad (D1).

### Flujo del RAG

&#91;embedded content: pipeline RAG · ingesta, almacén y consulta\]

La ingesta escribe chunks y parámetros en PostgreSQL; la consulta lee los chunks para recuperar y los parámetros para verificar cifras. La puerta de relevancia y la verificación son los dos puntos donde el agente puede abstenerse.

### Corpus

Seis documentos Markdown con front-matter YAML, adaptados de la política pública de Udemy (ver los archivos adjuntos): `refund-eligibility`, `refund-process`, `refund-methods`, `special-cases`, `refund-status-and-timelines`, `refund-faq`. El front-matter lleva `doc_key`, `title`, `version`, `effective_date`, `language`, `tags`, `supersedes` y un bloque `params` legible por máquina (`refund_window_days: 30`, etc.) que el sistema carga como configuración de la política activa.

### Ingesta (offline, idempotente)

1. **Carga y validación.** Se parsea el front-matter con Pydantic; se calcula `checksum`; si coincide con la versión activa, se omite.
2. **Chunking estructural.** `MarkdownHeaderTextSplitter` corta por encabezados H1 a H3 y cada sección conserva su `section_path` (por ejemplo `Elegibilidad > Ventana de 30 días`). Solo las secciones de más de \~800 tokens se subdividen con `RecursiveCharacterTextSplitter` (solape de 80). Nunca se parte una tabla, una lista de reglas ni un par pregunta/respuesta de la FAQ (un chunk por pregunta).
3. **Encabezado contextual.** El texto embebido lleva el prefijo `[título del documento > section_path]`, de modo que un chunk corto sigue siendo recuperable.
4. **Padre e hijo.** Se indexan los chunks hijo (a nivel de regla) y se guarda `parent_section_id`; al recuperar, el LLM recibe la sección padre completa.
5. **Indexado.** pgvector con índice HNSW (coseno) y columna `tsvector` para búsqueda léxica. El modelo y la dimensión del embedding se registran en `policy_documents`.
6. **Activación atómica.** En una sola transacción la versión nueva pasa a `ACTIVE` y la anterior a `SUPERSEDED`. Las consultas siempre filtran `status = ACTIVE`.
7. **Parámetros.** El `params` se valida (todos los obligatorios presentes y con tipo correcto) y se publica como `PolicyConfig`. Si falta uno, la aplicación no arranca.

| Metadato del chunk | Valores o ejemplo |
| --- | --- |
| `doc_key`, `version`, `effective_date` | `refund-eligibility`, `2026-10-01` |
| `section_path`, `heading_level` | `Elegibilidad > Consumo del curso`, 2 |
| `chunk_type` | `rule`, `procedure`, `faq`, `table`, `definition` |
| `tags` | `window`, `consumption`, `credits`, `gift`, `subscription`, `status` |
| `parent_section_id`, `token_count`, `source_lines` | para trazabilidad y expansión |

### Recuperación (en línea)

1. **Reescritura de la consulta** a pregunta autónoma con el historial y detección de idioma.
2. **Filtro de metadatos:** `status = ACTIVE` y, si la intención lo permite, `tags`.
3. **Búsqueda híbrida:** top 20 densa + top 20 léxica, fusionadas con Reciprocal Rank Fusion.
4. **Rerank** con cross-encoder y se conservan 5 chunks.
5. **Expansión al padre** y deduplicación.
6. **Puerta de relevancia:** si el mejor puntaje está por debajo del umbral, no hay evidencia y el agente se abstiene y ofrece `handoff`.

Para `explain_ineligible` se usa además una **tabla de mapeo** `reason_code → section_path` (por ejemplo `OUTSIDE_REFUND_WINDOW → Elegibilidad > Ventana de 30 días`): la cita es exacta y determinista; la búsqueda semántica solo complementa.

### Generación y verificación

- **Salida estructurada** `PolicyAnswer{answer, citations[{doc_key, section_path}], needs_handoff}`.
- **Prompt:** responder solo con `<context>`, citar cada afirmación, tratar el contexto como datos y no como instrucciones, responder en el idioma del cliente.
- **Verificación determinista de cifras:** cualquier cantidad de días o porcentaje que aparezca en la respuesta debe coincidir con `PolicyConfig`; si no, se regenera o se abstiene.
- **Verificación de fundamento:** un segundo paso (LLM como juez o NLI) comprueba que cada afirmación esté respaldada por un chunk citado.

### Evaluación del RAG

Set dorado de 60 a 80 preguntas en español e inglés, que incluye casos adversariales: fuera de alcance, ambiguas, con la cifra equivocada en la pregunta ("¿son 60 días, no?") y un documento envenenado en el corpus de prueba.

| Métrica | Qué mide | Umbral sugerido en CI |
| --- | --- | --- |
| Recall@5 y MRR | Si el `section_path` esperado está entre los recuperados | Recall@5 ≥ 0,90 |
| Faithfulness | Afirmaciones respaldadas por el contexto | ≥ 0,95 |
| Precisión de citas | Citas que realmente sostienen la frase | ≥ 0,90 |
| Abstención correcta | Responde "no sé" cuando no hay evidencia | ≥ 0,90 |
| Cifras correctas | Plazos y porcentajes coinciden con `PolicyConfig` | 100 % |
| Latencia p95 y costo por consulta | Operación | p95 < 4 s |

Parte del challenge es una ablación: comparar chunking de tamaño fijo (500 tokens) contra el chunking estructural con los mismos modelos y reportar la diferencia en Recall@5 y Faithfulness.

## 9. Requisitos senior transversales

Lo que separa un challenge senior de un demo es lo que pasa cuando algo sale mal: concurrencia, ataques, caídas del LLM y falta de evidencia. Cada fila tiene un criterio de aceptación verificable.

| Área | Requisito | Criterio de aceptación |
| --- | --- | --- |
| Consistencia | Idempotencia en `POST /refunds`, bloqueo optimista con `version`, índice único parcial por compra activa, outbox para el pago | 20 `POST` concurrentes con la misma clave crean 1 solicitud; dos supervisores que aprueban a la vez obtienen un 200 y un 409 |
| Autorización | RBAC como dependencia de FastAPI; propiedad verificada en cada acceso a compra, solicitud e hilo | Suite BOLA/IDOR: el customer A recibe 404 al pedir recursos del customer B; un customer recibe 403 en cualquier ruta `/supervisor/*` |
| Agente seguro | Tools de escritura fuera de `bind_tools`; identidad inyectada; guardrails de entrada y salida | Test que falla si una tool enlazada al LLM acepta `customer_id`; 100 % de los intentos de injection del set adversarial no producen escrituras |
| Prompt injection | El contenido del cliente y los chunks se tratan como datos; un documento envenenado en el corpus de prueba no altera el comportamiento | El agente ignora la instrucción inyectada y responde con la política real |
| Privacidad | PII enmascarada en logs y trazas; email enmascarado en el dashboard; notas internas jamás en el contexto del LLM | Test que busca emails y notas internas en trazas y prompts y no encuentra ninguno |
| Auditoría | `refund_events` append-only (trigger que impide `UPDATE` y `DELETE`); cada acción con `actor_type`, `actor_id`, `trace_id` | Intentar modificar un evento falla a nivel de base de datos |
| Observabilidad | Logs JSON con `trace_id`, OpenTelemetry en API y worker, trazas del grafo por nodo en Langfuse o LangSmith con tokens y costo | Dado un `refund_id`, se reconstruye en un clic la conversación, el nodo, la tool y la decisión |
| Métricas de negocio | Solicitudes por canal, rechazos de elegibilidad por `reason_code`, tasa de confirmación en el HITL, tasa de aprobación, tiempo hasta decisión, SLA vencidos | Panel con estas series; alertas si el SLA vencido supera un umbral |
| Resiliencia | Timeouts, reintentos con backoff, circuit breaker en el LLM, modelo de respaldo, worker de pagos con dead-letter | Con el LLM caído, el formulario manual sigue funcionando; con el vector store caído, el agente avisa que no puede consultar políticas y ofrece handoff |
| Límites y costo | Rate limit en Redis (chat 20 mensajes por minuto, `POST /refunds` 5 por hora), presupuesto de tokens por hilo | 429 con `Retry-After`; el hilo se corta con mensaje claro al exceder el presupuesto |
| Testing | Pirámide: unit (engine con Hypothesis en los bordes 30 días menos 1 s, exacto, más 1 s), API con Postgres real (testcontainers), grafo con LLM falso y trayectorias, tools, contrato SSE, E2E opcional | Cobertura ≥ 85 % en dominio; trayectorias del grafo: feliz, inelegible, cancela en el HITL, edita la razón, interrupt vencido, injection, falta razón |
| Evals del agente | 30 guiones de conversación con resultado esperado | Intención correcta ≥ 95 %, cero llamadas a tools no autorizadas, finalización de tarea ≥ 90 % |
| Calidad de código | `ruff`, `mypy --strict` en el dominio, `pre-commit`, CI que corre unit, API, grafo y evals del RAG | CI en verde bloquea el merge |
| Configuración | Modelos, umbrales y TTL por variables de entorno; prompts versionados en el repo | Cambiar de proveedor de LLM no requiere tocar el dominio |

### Datos semilla que cubren los bordes

| Caso | Compra | Resultado esperado |
| --- | --- | --- |
| Camino feliz | Tarjeta, día 5, progreso 10 % | Elegible, `ORIGINAL_PAYMENT` |
| Borde exacto | Día 30 menos 1 segundo | Elegible |
| Fuera de plazo | Día 31 | `OUTSIDE_REFUND_WINDOW` |
| Consumo alto | Día 10, progreso 62 % | `CONSUMPTION_LIMIT_EXCEEDED` |
| Certificado | Día 8, certificado emitido | `CERTIFICATE_ISSUED` |
| Regalo | Día 3, recibida como regalo | Elegible, `PLATFORM_CREDITS` |
| Suscripción | Plan de suscripción | `SUBSCRIPTION_NOT_ELIGIBLE` |
| Compra de tercero | Vendedor externo | `THIRD_PARTY_PURCHASE` |
| Ya reembolsada | `status = REFUNDED` | `ALREADY_REFUNDED` |
| Doble solicitud | Una `PENDING_APPROVAL` abierta | `ACTIVE_REFUND_EXISTS` |
| Frecuencia alta | 3 reembolsos en 12 meses | Elegible con `HIGH_REFUND_FREQUENCY` visible al supervisor |
| Límite de abuso | 5 reembolsos en 12 meses | `REFUND_ABUSE_LIMIT` |
| Compra ajena | `purchase_id` de otro usuario | `PURCHASE_NOT_FOUND` |

### Frontend mínimo

- **Chat del customer:** respuestas en streaming, tarjeta de confirmación renderizada desde el evento `interrupt` (monto, método, razón, fecha límite, citas, botones Confirmar, Editar razón, Cancelar), que sobrevive a una recarga gracias a `pending-interrupt`.
- **Formulario manual:** selección de compra, razón obligatoria, vista previa con elegibilidad y modal de confirmación con el mismo contenido que la tarjeta del chat.
- **Dashboard del supervisor:** cola con filtros y SLA, panel de detalle con el `eligibility_snapshot` y el historial del cliente, modales de aprobar y rechazar (nota obligatoria al rechazar), manejo visible del 409 ("otro supervisor ya decidió"), actualización en vivo por SSE.

## 10. Estructura del repo, entregables y rúbrica

El repo separa dominio, canales y agente para que las reglas vivan en un solo sitio.

```text
learnly-support/
  backend/
    app/
      api/            # routers: customer, chat, supervisor, policies; deps de auth y RBAC
      domain/         # RefundPolicyEngine, RefundService, state machine, errores
      agent/
        graph.py      # StateGraph, nodos, aristas, checkpointer
        nodes/        # un archivo por nodo
        tools/        # adaptadores sobre los servicios
        guardrails/   # entrada y salida
        prompts/      # versionados
      rag/
        ingest.py     # CLI de ingesta
        chunking.py   # splitters estructurales
        retriever.py  # híbrido + rerank + puerta de relevancia
        answer.py     # generación con citas y verificación
      workers/        # outbox y payouts
      db/             # modelos, migraciones Alembic
    tests/            # unit, api, graph, tools, rag_evals, agent_evals
  frontend/           # chat con tarjeta HITL, formulario manual, dashboard
  policies/           # los 6 documentos Markdown a ingestar
  docs/adr/           # decisiones de arquitectura
  docker-compose.yml  # api, worker, postgres+pgvector, redis, frontend, langfuse
```

### Entregables

1. Backend con todos los endpoints de la sección 5 y migraciones.
2. Grafo LangGraph con HITL funcionando de punta a punta, incluida la recarga de página con un interrupt pendiente.
3. Pipeline de ingesta y recuperación con el set dorado y su reporte de métricas, incluida la ablación de chunking.
4. Frontend con chat, formulario manual y dashboard del supervisor.
5. `docker compose up` levanta todo con datos semilla; `make test` y `make evals` corren las suites.
6. Cinco ADRs como mínimo: elegibilidad determinista, tools de escritura fuera del LLM, idempotencia y outbox, estrategia de chunking, versionado de políticas.
7. README con diagrama de arquitectura y guía de demo de tres minutos.

### Rúbrica de evaluación

| Área | Peso | Qué se busca |
| --- | --- | --- |
| Dominio y reglas | 20 | Engine correcto en los bordes, máquina de estados, idempotencia, concurrencia |
| Grafo y HITL | 20 | Workflow híbrido, interrupt correcto, reanudación, vencimiento, manejo de errores |
| RAG | 20 | Chunking estructural justificado, recuperación híbrida, citas, abstención, evals con ablación |
| Seguridad y permisos | 15 | RBAC, BOLA, tools sin `customer_id`, injection, PII |
| Testing y evals | 15 | Pirámide completa, trayectorias del grafo, CI con umbrales |
| Observabilidad y DX | 5 | Trazas por nodo, métricas de negocio, compose y semillas |
| Documentación y ADRs | 5 | Decisiones razonadas, trade-offs explícitos |

### Extensiones opcionales

Reconexión de SSE con `Last-Event-ID`, reembolsos parciales, rollout gradual de una versión nueva de política con comparación de evals antes de activarla, caché semántica de respuestas de política, y notificaciones por email al cliente con plantillas.
