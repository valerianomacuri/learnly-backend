# Feature Specification: 09 · Agente: tools y grafo de lectura

**Feature**: `009-agente-lectura` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/09-agente-lectura.md`

**Depende de:** 07 (RAG), 03 (servicios) · **Refs:** challenge §2 (reglas del agente), §5 (chat), §6 (estado, nodos, persistencia), §7 (tools). Usar el subagente `ai-engineer`.

## Objetivo

Chat funcional para los caminos de **solo lectura**: preguntas de política con citas, estado de solicitudes, fuera de alcance y handoff. Establece la frontera de seguridad del agente (identidad inyectada, allowlist de tools) antes de darle ninguna capacidad de escritura.

## Alcance

- Dentro: `Principal` del agente, tools de lectura (1–5, 7, 8) + `escalate_to_human` (10), grafo parcial, checkpointer, endpoints de hilos y mensajes con SSE, degradación de clasificador.
- Fuera: flujo de reembolso y HITL (10); tools de escritura 6 y 9 (10); guardrails avanzados (12 — aquí solo guardrails mínimos: longitud y filtro de salida básico).

## Criterios de aceptación

- Pregunta de política: respuesta con citas reales y sin cifras fuera de `PolicyConfig`.
- "¿Cómo va mi solicitud?" lista solo solicitudes propias, sin notas internas.
- Ninguna tool enlazada al LLM acepta `customer_id` ni `role` (test automático).
- Recargar la página y pedir `/messages` reconstruye el historial desde el checkpointer.
