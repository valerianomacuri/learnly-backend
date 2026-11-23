# Tasks: 06 · Frontend: formulario manual y dashboard del supervisor

**Feature**: `006-frontend-base` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/06-frontend-base.md`

## Tareas

- [x] Instalar y configurar Tailwind, router, query, tipos OpenAPI.
- [x] Endpoints de políticas y SSE + tests (`tests/test_policies_api.py`, `test_supervisor_stream.py`).
- [x] Auth y guardas de rol (customer no ve `/supervisor`; supervisor no ve chat/formulario).
- [x] Formulario + modal + mis solicitudes (con timeline público).
- [x] Dashboard + detalle + modales + 409 + SSE.
- [x] Tests de frontend con Vitest + Testing Library para formulario (validación de razón, token) y modal de rechazo; (Playwright E2E opcional, queda para 13).
- [x] Script `docs/demo-sin-llm.md` actualizado con el recorrido en UI.
