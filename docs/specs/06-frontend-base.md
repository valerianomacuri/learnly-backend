# 06 · Frontend: formulario manual y dashboard del supervisor

**Depende de:** 05 · **Refs:** challenge §5, §9 (Frontend mínimo: formulario y dashboard)
**Código en:** `../../learnly-frontend` (Vite + React 19 + TypeScript) · **Hito: producto usable sin chat**

## Objetivo
Interfaz completa para el camino sin LLM: el customer solicita un reembolso por formulario y el supervisor decide desde un dashboard con actualización en vivo.

## Alcance
- Dentro: Tailwind, login, rutas por rol, cliente API tipado, formulario con preview y modal, mis solicitudes, cola del supervisor, detalle, modales, manejo de 409, SSE, visor de políticas. Backend: `GET /supervisor/refunds/stream` y `GET /policies`, `GET /policies/{doc_key}`.
- Fuera: chat (11).

## Cambios

### Backend (pequeño)
- `GET /policies`, `/policies/{doc_key}` (`policy:read`): documentos ACTIVE con versión, fecha y Markdown (visor sin LLM).
- `GET /supervisor/refunds/stream` (SSE, `refund:read_any`): emite `refund.created|updated` con `{id, status, version}`. Fuente de eventos: `LISTEN/NOTIFY` de Postgres disparado desde `RefundEventService.append` (sin Redis hasta el 12). Cabecera `Cache-Control: no-store`, heartbeat cada 15 s.
- CORS configurable (`CORS_ORIGINS`) para `localhost:5173`. Auth del SSE: el navegador no envía `Authorization` con `EventSource`; usar `fetch` + `ReadableStream` en el cliente (así se mantiene Bearer) o un ticket corto en query — **decidir fetch-stream** y reutilizarlo en el spec 11.

### Frontend
- Dependencias: `tailwindcss` (plugin `@tailwindcss/vite`), `react-router`, `@tanstack/react-query`, `zod` (validación de respuestas). Sin librería de componentes; componentes propios mínimos (`Button`, `Modal`, `Badge`, `Table`).
- Estructura: `src/api/` (cliente `fetch` con Bearer, manejo `problem+json` → `ApiError{code, reasonCodes}`; tipos generados a mano desde los schemas o con `openapi-typescript` desde `/openapi.json` — **preferido**), `src/auth/` (login, token en memoria + `sessionStorage`, `RequireRole`), `src/features/{purchases,refunds,supervisor,policies}/`, `src/components/`.
- Rutas: `/login`; customer: `/purchases`, `/refunds`, `/refunds/new?purchase=`; supervisor: `/supervisor` (cola), `/supervisor/refunds/:id`, `/policies`.
- **Formulario**: selección de compra (muestra días restantes) → categoría + texto (contador 20–1000) → "Ver elegibilidad" (`/refunds/preview`) → si inelegible, muestra `reason_codes` traducidos y citas; si elegible, **modal de confirmación** (monto, método, razón, fecha límite, citas) → `POST /refunds` con `Idempotency-Key` (UUID generado al abrir el modal, reutilizado en reintentos).
- **Dashboard**: tabla con filtros (estado, SLA vencido, riesgo, canal, razón, fechas, búsqueda), SLA resaltado, orden y cursor; panel de detalle con `eligibility_snapshot`, compra, progreso, historial del cliente, flags, notas internas; modales aprobar (nota opcional) / rechazar (nota ≥10, validada en cliente y servidor); envía `If-Match`; en **409** muestra "otro supervisor ya decidió" y recarga el detalle; estado vivo por SSE (invalida queries). Botón "Reintentar pago" si `FAILED`.

## Tareas
- [x] Instalar y configurar Tailwind, router, query, tipos OpenAPI.
- [x] Endpoints de políticas y SSE + tests (`tests/test_policies_api.py`, `test_supervisor_stream.py`).
- [x] Auth y guardas de rol (customer no ve `/supervisor`; supervisor no ve chat/formulario).
- [x] Formulario + modal + mis solicitudes (con timeline público).
- [x] Dashboard + detalle + modales + 409 + SSE.
- [x] Tests de frontend con Vitest + Testing Library para formulario (validación de razón, token) y modal de rechazo; (Playwright E2E opcional, queda para 13).
- [x] Script `docs/demo-sin-llm.md` actualizado con el recorrido en UI.

## Criterios de aceptación
- Recorrido manual: customer crea solicitud → aparece en vivo en el dashboard → supervisor aprueba → worker → customer ve `COMPLETED`.
- Dos pestañas de supervisor: la segunda en decidir ve el aviso de 409 sin romper la UI.
- Rechazar con nota corta se bloquea en cliente y, si se fuerza, el 422 se muestra.
- `npm run build`, `npm run lint` y `npm test` en verde.

## Verificación
```bash
(cd ../learnly-frontend && npm i && npm run lint && npm test && npm run build)
uv run pytest tests/test_policies_api.py tests/test_supervisor_stream.py -q
# dev: backend (uvicorn --loop asyncio:SelectorEventLoop) + worker + `npm run dev`; recorrer el guion de la demo
```
