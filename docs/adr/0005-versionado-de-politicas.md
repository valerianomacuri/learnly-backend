# ADR 0005 · Versionado de políticas y reconstrucción de decisiones

**Estado:** aceptada · **Specs:** 02, 07 · **Challenge:** D2, D8

## Contexto
Las reglas de reembolso cambian (un plazo pasa de 30 a 45 días, un nuevo caso especial). Una solicitud decidida hoy debe poder explicarse mañana con las reglas **vigentes cuando se evaluó**, no con las actuales. Además, el texto que lee el cliente (y que el RAG cita) y los números que ejecuta el motor no pueden divergir.

## Decisión
- Cada documento de `policies/*.md` lleva front-matter YAML (`doc_key`, `version`, `effective_date`, `supersedes`, `params`, `reason_code_map`). Los `params` son los umbrales ejecutables (ADR 0001); el cuerpo Markdown es lo que se ingesta para el RAG. **Una sola fuente de verdad.**
- `uv run policies-load` carga los documentos en `policy_documents`, idempotente por checksum. Mantiene **como máximo una versión `ACTIVE` por `doc_key`** (índice único parcial) y la activa de forma atómica en una transacción; la anterior queda como histórico.
- Al arrancar, `create_app` falla si no hay una política ACTIVE completa: la API no sirve con reglas a medias.
- Cada evaluación guarda `policy_version` y un snapshot (`eligibility_checks`, `refund_requests.eligibility_snapshot`) con los hechos y el resultado. El detalle del supervisor muestra con qué versión se decidió.
- `rag-ingest` no hace nada si coinciden checksum, estrategia de chunking y modelo de embeddings; si cambia algo, calcula primero los embeddings y luego activa y reemplaza los chunks en una sola transacción (el RAG nunca queda a medias).

## Alternativas consideradas
1. **Umbrales en settings/variables de entorno.** Sin historial y desacoplados del texto citado.
2. **Tabla de reglas editable por UI.** Pierde la revisión por pull request y el texto legible como fuente.
3. **Rollout gradual con comparación de evals antes de activar** (extensión del challenge). Útil, pero fuera de alcance: el cambio de versión aquí es inmediato y atómico.

## Consecuencias
- (+) Cualquier decisión se reconstruye con su versión; cambiar una regla es un pull request sobre un documento.
- (+) Los evals del RAG (spec 08) corren contra la misma versión que la API.
- (−) Una versión nueva no se previsualiza contra solicitudes en curso: las existentes conservan su snapshot, pero no hay "dry-run" de impacto.
- (−) Un front-matter inválido bloquea la carga (es deliberado: falla antes de servir).
