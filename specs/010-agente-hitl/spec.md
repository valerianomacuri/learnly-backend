# Feature Specification: 10 · Agente: flujo de reembolso con HITL

**Feature**: `010-agente-hitl` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/10-agente-hitl.md`

**Depende de:** 09 · **Refs:** challenge §2 (reglas 3 y 4), §6 (ruteo, interrupt, reanudación), §7 (tools 4, 5, 6, 9), BR-12/13/14, D3/D4. Usar el subagente `ai-engineer`.

## Objetivo

El agente puede crear y cancelar solicitudes **solo** tras confirmación humana explícita, con las tools de escritura fuera de `bind_tools`. Recargar la página con un interrupt pendiente recupera la tarjeta.

## Alcance

- Dentro: nodos del flujo de dinero, `interrupt()`, `/resume`, `/pending-interrupt`, tools 6 y 9, idempotencia, interrupt vencido.
- Fuera: tarjeta de UI (11); guardrails avanzados/injection set (12).

## Criterios de aceptación

- Es imposible crear una solicitud sin pasar por `interrupt()` y `confirm` (test por trayectoria y test de que `create_refund` no es alcanzable desde otra arista).
- Mensaje libre con interrupt pendiente → 409 `INTERRUPT_PENDING` con el payload.
- Recarga: `pending-interrupt` devuelve la misma tarjeta.
- Todas las trayectorias del §9 pasan con LLM falso.
