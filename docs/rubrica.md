# Autoevaluación contra la rúbrica (challenge §10)

Puntaje propio, con la evidencia y **lo que falta o está sin verificar**. No es una nota oficial.

| Área | Peso | Autoevaluación | Evidencia | Límites conocidos |
| --- | --- | --- | --- | --- |
| Dominio y reglas | 20 | Sólido | `RefundPolicyEngine` puro con Hypothesis en los bordes ([ADR 0001](adr/0001-elegibilidad-determinista.md)); máquina de estados; idempotencia, `FOR UPDATE`, `If-Match` y outbox ([ADR 0003](adr/0003-idempotencia-y-outbox.md)); tests de 20 `POST` concurrentes y de doble aprobación | Pagos simulados; sin reembolsos parciales (extensión) |
| Grafo y HITL | 20 | Sólido | Workflow híbrido con `interrupt()`, reanudación tras reinicio, expiración, cancelación y edición ([ADR 0002](adr/0002-tools-de-escritura-fuera-del-llm.md)); `tests/agent/test_hitl.py`, trayectorias; 30 guiones en `make agent-evals` | Los guiones corren con un LLM falso: miden el cableado y el ruteo (la intención sale del clasificador por palabras clave), no la calidad del modelo real. `agent-evals-live` existe pero no se ejecutó en este entorno |
| RAG | 20 | Bueno | Chunking estructural, híbrido + RRF, puerta de relevancia, citas verificadas, abstención, documento envenenado; 79 preguntas doradas y ablación ([ADR 0004](adr/0004-chunking.md)) | La ablación estructural vs. `fixed_500` es sobre un corpus de ~15 KB: Recall@5 no discrimina y estructural no gana ahí. El set dorado se amplió tras ver resultados (documentado). Cifras "full" del 2026-11-15; no se repitieron en el spec 13 |
| Seguridad y permisos | 15 | Sólido | RBAC por permisos, BOLA/IDOR (`tests/security`, tabla viva de rutas), tools sin `customer_id`, 46 casos adversariales con un LLM obediente (cero escrituras y cero fugas), guardrails, PII enmascarada en logs y en trazas (`tests/agent/test_tracing.py`), rate limit y presupuesto de tokens | Los guardrails de inyección y el scrub de PII son por patrones: no cubren todo formato. Sin pentest externo |
| Testing y evals | 15 | Sólido | Pirámide: `tests/unit` (dominio), API con Postgres real, grafo con LLM falso, seguridad, evals del RAG y del agente con umbrales en `evals/thresholds.toml`; CI con puertas (cobertura de `domain/` ≥ 85 %, intención ≥ 95 %, 0 tools no autorizadas, finalización ≥ 90 %) | **El workflow de CI no se ha ejecutado en GitHub** (no hay remoto aquí); se validó localmente cada comando. No hay E2E de navegador (opcional en el challenge). Los tests con Postgres usan el contenedor del compose, no testcontainers |
| Observabilidad y DX | 5 | Bueno | Logs JSON con `trace_id`; OTel (apagado por defecto); LangSmith opt-in con scrub y fail-open ([ADR 0008](adr/0008-langsmith-para-trazas.md)); `/healthz`, `/readyz`, `/metrics` y métricas de negocio; alertas de ejemplo y dashboard Grafana; `docker compose up` con `init` (migra, políticas, ingesta, semilla); Makefile | La integración real con LangSmith y el stack `observability` no se probaron contra servicios reales aquí (solo con tracer/cliente simulados). El `refund_id` no se etiqueta en el run: la unión es `trace_id` ↔ `metadata.request_id`. La prueba "desde un clon limpio en otra máquina" queda pendiente |
| Documentación y ADRs | 5 | Bueno | 5 ADRs mínimos + 0008 con alternativas y consecuencias; README con diagrama Mermaid, comandos y guía de demo ([demo.md](demo.md)); `CLAUDE.md` y specs al día | ADR 0006 y 0007 opcionales: ver [0006](adr/0006-refund-events-unica-auditoria.md) y [0007](adr/0007-vite-en-lugar-de-nextjs.md), breves |

## Entregables del challenge (§10)

| # | Entregable | Estado |
| --- | --- | --- |
| 1 | Backend con los endpoints de la §5 y migraciones | Hecho (specs 01-05, 09-10) |
| 2 | Grafo con HITL de punta a punta, incluida la recarga con interrupt pendiente | Hecho (spec 10-11) |
| 3 | Ingesta y recuperación con set dorado, reporte y ablación | Hecho (specs 07-08) con la salvedad del corpus pequeño |
| 4 | Frontend con chat, formulario y dashboard | Hecho (repo `learnly-frontend`, specs 06 y 11) |
| 5 | `docker compose up` + `make test` y `make evals` | Compose y Makefile hechos; ver "sin verificar" abajo |
| 6 | Cinco ADRs mínimos | Hecho |
| 7 | README con diagrama y guía de demo de 3 minutos | Hecho |

## Sin verificar en este entorno
- `docker compose up` desde cero: la sintaxis del compose y la secuencia están escritas, pero la corrida completa (build de imágenes, `init`, frontend) se debe confirmar en una máquina limpia con Docker.
- GitHub Actions: el workflow no se ejecutó. Los comandos de cada job sí se corrieron localmente.
- Modo real de los evals (RAG "full" y agente `--real`): requieren `OPENAI_API_KEY`.
- LangSmith y OTel contra servicios reales.
