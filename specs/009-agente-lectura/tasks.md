# Tasks: 09 · Agente: tools y grafo de lectura

**Feature**: `009-agente-lectura` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/09-agente-lectura.md`

## Tareas

- [x] Tests primero: registro de tools (sin `customer_id`/`role`, sin tools prohibidas), BOLA de tools (ajeno → `PURCHASE_NOT_FOUND`), `ToolResult` de error.
- [x] Factory LLM + `FakeChatModel`; fixtures de grafo.
- [x] Servicios/tools de lectura y `escalate_to_human`.
- [x] Nodos y ensamblado del grafo; checkpointer Postgres (tablas del saver vía `setup()` en migración/arranque).
- [x] Endpoints + SSE + test de contrato de eventos.
- [x] Trayectorias con LLM falso: pregunta de política con cita; estado de una solicitud; fuera de alcance → handoff; sin evidencia → "no sé" + handoff; clasificador con fallo ×2 → palabras clave.
- [x] Test de aislamiento: hilo ajeno → 404; el supervisor no puede usar `/chat` (403).
- [x] Prueba manual con OpenAI real (`live`): `uv run pytest -m live tests/agent` (`tests/agent/test_agent_live.py`, requiere `OPENAI_API_KEY`).
