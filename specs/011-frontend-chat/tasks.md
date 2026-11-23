# Tasks: 11 · Frontend del chat con tarjeta HITL

**Feature**: `011-frontend-chat` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/11-frontend-chat.md`

## Tareas

- [x] Parser SSE con tests (chunks partidos a mitad de línea, `data` multilínea, `error`).
- [x] `ChatPage`, `MessageList`, `Composer`, `ConfirmationCard` + tests de componente (botones, expiración, edición, 409).
- [x] Recuperación tras recarga (test con MSW simulando `pending-interrupt`).
- [x] Navegación: customer ve "Chat" y "Nuevo reembolso"; supervisor no ve el chat (guardas de ruta ya existen).
- [x] Recorrido E2E manual documentado en `docs/demo.md` (Playwright opcional: no implementado).
