# Feature Specification: 06 · Frontend: formulario manual y dashboard del supervisor

**Feature**: `006-frontend-base` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/06-frontend-base.md`

**Depende de:** 05 · **Refs:** challenge §5, §9 (Frontend mínimo: formulario y dashboard)
**Código en:** `../learnly-frontend` (Vite + React 19 + TypeScript) · **Hito: producto usable sin chat**

## Objetivo

Interfaz completa para el camino sin LLM: el customer solicita un reembolso por formulario y el supervisor decide desde un dashboard con actualización en vivo.

## Alcance

- Dentro: Tailwind, login, rutas por rol, cliente API tipado, formulario con preview y modal, mis solicitudes, cola del supervisor, detalle, modales, manejo de 409, SSE, visor de políticas. Backend: `GET /supervisor/refunds/stream` y `GET /policies`, `GET /policies/{doc_key}`.
- Fuera: chat (11).

## Criterios de aceptación

- Recorrido manual: customer crea solicitud → aparece en vivo en el dashboard → supervisor aprueba → worker → customer ve `COMPLETED`.
- Dos pestañas de supervisor: la segunda en decidir ve el aviso de 409 sin romper la UI.
- Rechazar con nota corta se bloquea en cliente y, si se fuerza, el 422 se muestra.
- `npm run build`, `npm run lint` y `npm test` en verde.
