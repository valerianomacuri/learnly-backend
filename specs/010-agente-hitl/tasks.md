# Tasks: 10 · Agente: flujo de reembolso con HITL

**Feature**: `010-agente-hitl` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/10-agente-hitl.md`

## Tareas

- [x] Tests de herramientas: la tool 6 rechaza si falta `confirmation_proof` o si el hash no coincide; la tool 9 solo en `PENDING_APPROVAL`; ambas fuera de `LLM_BOUND_TOOLS`.
- [x] Trayectorias del grafo con LLM falso (cada una un test): **feliz** (pide → razón → elegible → interrupt → confirm → solicitud `CHAT` creada), **inelegible** (explica con cita, 0 escrituras), **cancela en el HITL** (0 escrituras), **edita la razón** (vuelve a recolectar), **interrupt vencido** (tarjeta nueva), **falta razón** (no llega a confirmación), **injection** dentro de `reason_text` (no cambia monto/método/compra; la confirmación muestra el payload real).
- [x] Idempotencia: reintentar el nodo `create_refund` tras un fallo no duplica (misma `Idempotency-Key`).
- [x] Endpoints `resume`/`pending-interrupt`, evento SSE `interrupt`, 409s.
- [x] Reanudación tras reinicio del proceso: crear interrupt, reiniciar app (nuevo grafo/checkpointer), `resume` funciona.
- [x] Verificar que la solicitud creada por el agente aparece en la cola del supervisor con `channel=CHAT`.
