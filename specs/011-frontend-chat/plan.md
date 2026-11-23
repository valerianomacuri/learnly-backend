# Implementation Plan: 11 · Frontend del chat con tarjeta HITL

**Feature**: `011-frontend-chat` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/11-frontend-chat.md`

Spec: [spec.md](spec.md) · Tareas: [tasks.md](tasks.md) · Constitution: [`.specify/memory/constitution.md`](../../.specify/memory/constitution.md)

## Cambios

- `src/features/chat/`: `useChatStream` (parser SSE: `token`, `tool_call`, `tool_result`, `interrupt`, `final`, `error`; cancela con `AbortController`), `ChatPage` (lista de hilos + conversación), `MessageList` (burbujas, indicador de tool en curso, citas como chips con `doc_key > section_path` enlazados al visor de políticas), `Composer` (deshabilitado mientras hay interrupt pendiente o stream activo).
- `ConfirmationCard` (renderiza el payload del `interrupt`): monto, método, razón, fecha límite, citas, contador de expiración; acciones → `POST /resume` con `{interrupt_id, decision}`; **Editar razón** abre un campo (20–1000 chars) y envía `decision:"edit"`. Tras expirar, botones deshabilitados y mensaje que invita a reiniciar (el servidor emite tarjeta nueva).
- Al montar un hilo: `GET /messages` + `GET /pending-interrupt` (204 → nada) para repintar la tarjeta tras recargar. Manejo de **409 `INTERRUPT_PENDING`** (resalta la tarjeta, no pierde el mensaje) y **429** (muestra `Retry-After`).
- Errores del stream (`error`) con reintento manual; estado "el agente no está disponible, usa el formulario" con enlace al formulario del 06 (degradación visible).
- Tipos de eventos SSE con `zod` discriminado por `event`.

## Verificación

```bash
(cd ../learnly-frontend && npm run lint && npm test && npm run build)
# manual: backend + worker + frontend; dos navegadores (customer y supervisor); guion docs/demo.md
```
