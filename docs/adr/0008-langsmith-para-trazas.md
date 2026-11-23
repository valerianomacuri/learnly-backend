# ADR 0008 · LangSmith (opt-in) para las trazas del grafo; OpenTelemetry para el resto

**Estado:** aceptada · **Specs:** 13 · **Challenge:** §9 Observabilidad, Privacidad

## Contexto
Hay que poder reconstruir, dado un `refund_id`, la conversación → nodo → tool → decisión, con tokens y costo. Las trazas de LangGraph por nodo con costo por LLM no salen gratis de OpenTelemetry. A la vez, una traza de LLM contiene texto del cliente: enviarla a un SaaS es publicar datos personales.

## Decisión
- **Logs JSON** (`core/logging.py`) con `trace_id` y `user_id` (id, nunca email) y filtro de PII. El `trace_id` (cabecera `X-Request-ID`) se copia a `refund_events.payload.trace_id` y al payload del outbox; el worker lo restaura.
- **OpenTelemetry** (FastAPI + SQLAlchemy, API y worker) con exportador OTLP/HTTP, **apagado por defecto** (`OTEL_ENABLED`).
- **LangSmith** para las trazas del grafo, **opt-in** (`TRACING_PROVIDER=none|langsmith`, por defecto `none`: sin clave no hay red). Se adjunta un `LangChainTracer` explícito en `AgentRuntime._run` junto al `UsageCallback`; no se usa `LANGSMITH_TRACING` global (trazaría RAG y evals, y se saltaría el scrub). No se tocan nodos, tools ni grafo.
- **Privacidad:** el cliente se crea con `hide_inputs`/`hide_outputs` que enmascaran PII y eliminan token de confirmación, hash y notas internas (`agent/tracing.scrub`). Si el scrub falla, no se envía nada (nunca el dato crudo).
- **Fail-open:** un tracer que falla se registra y nunca rompe la petición.
- **Correlación:** `request_id` va como metadata del run; `refund_events.payload.trace_id` ↔ filtro `metadata.request_id` en LangSmith. El `refund_id` no se etiqueta en el run (el nodo de escritura no se modifica).
- **Métricas** de negocio con `prometheus-client` en `/metrics` (token opcional) y reglas de alerta de ejemplo en `ops/alerts.yml`.

## Alternativas consideradas
1. **Langfuse** (self-host). Mejor control de datos; más infraestructura (Postgres + ClickHouse + web). Viable si los datos no pueden salir; el diseño (callback aditivo + scrub) lo permite cambiando solo `build_tracer`.
2. **Solo OpenTelemetry** para el grafo. Sin costo por LLM ni vista por nodo lista para usar; habría que construirla.
3. **LangSmith en modo global** (`LANGSMITH_TRACING=true`). Más simple, pero sin scrub propio y con trazas de RAG/evals.

## Consecuencias
- (+) Trazas por nodo y costo reales cuando se activa; cero efecto cuando no.
- (−) **Los datos salen a un SaaS** (o a la instancia self-hosted de `LANGSMITH_ENDPOINT`): por eso es opt-in y el scrub es obligatorio. El scrub es por patrones: un dato personal en formato inesperado puede pasar; no sustituye una revisión de privacidad antes de activarlo en producción.
- (−) El costo que ve el panel Prometheus (`refund_llm_cost_usd_total`) es una estimación con precio fijo por token; el exacto lo calcula LangSmith.
