# 11 · Frontend del chat con tarjeta HITL

**Depende de:** 06, 10 · **Refs:** challenge §9 (Frontend mínimo: chat), §6 (payload del interrupt) · **Hito: flujo completo con chat**
**Código en:** `../../learnly-frontend`

## Objetivo
Widget de chat para el customer con respuestas en streaming y tarjeta de confirmación (Confirmar / Editar razón / Cancelar) que sobrevive a una recarga.

## Alcance
- Dentro: página `/chat`, cliente SSE (`fetch` + `ReadableStream`, reutiliza el del spec 06), tarjeta HITL, historial, recuperación de interrupt.
- Fuera: reconexión con `Last-Event-ID` (extensión opcional).

## Cambios
- `src/features/chat/`: `useChatStream` (parser SSE: `token`, `tool_call`, `tool_result`, `interrupt`, `final`, `error`; cancela con `AbortController`), `ChatPage` (lista de hilos + conversación), `MessageList` (burbujas, indicador de tool en curso, citas como chips con `doc_key > section_path` enlazados al visor de políticas), `Composer` (deshabilitado mientras hay interrupt pendiente o stream activo).
- `ConfirmationCard` (renderiza el payload del `interrupt`): monto, método, razón, fecha límite, citas, contador de expiración; acciones → `POST /resume` con `{interrupt_id, decision}`; **Editar razón** abre un campo (20–1000 chars) y envía `decision:"edit"`. Tras expirar, botones deshabilitados y mensaje que invita a reiniciar (el servidor emite tarjeta nueva).
- Al montar un hilo: `GET /messages` + `GET /pending-interrupt` (204 → nada) para repintar la tarjeta tras recargar. Manejo de **409 `INTERRUPT_PENDING`** (resalta la tarjeta, no pierde el mensaje) y **429** (muestra `Retry-After`).
- Errores del stream (`error`) con reintento manual; estado "el agente no está disponible, usa el formulario" con enlace al formulario del 06 (degradación visible).
- Tipos de eventos SSE con `zod` discriminado por `event`.

## Tareas
- [ ] Parser SSE con tests (chunks partidos a mitad de línea, `data` multilínea, `error`).
- [ ] `ChatPage`, `MessageList`, `Composer`, `ConfirmationCard` + tests de componente (botones, expiración, edición, 409).
- [ ] Recuperación tras recarga (test con MSW simulando `pending-interrupt`).
- [ ] Navegación: customer ve "Chat" y "Nuevo reembolso"; supervisor no ve el chat (guardas de ruta ya existen).
- [ ] Recorrido E2E manual documentado en `docs/demo.md`; Playwright opcional contra el backend con LLM falso (flag `LLM_PROVIDER=fake` solo en entorno de test).

## Criterios de aceptación
- Chat → "quiero un reembolso de <curso>" → pregunta de razón → tarjeta → Confirmar → mensaje de éxito → la solicitud aparece en vivo en el dashboard del supervisor con canal `CHAT`.
- Recargar la página con la tarjeta visible la restaura idéntica.
- Con el backend de LLM caído el chat muestra el error y ofrece el formulario; el formulario sigue funcionando.
- `npm run lint && npm test && npm run build` en verde.

## Verificación
```bash
(cd ../learnly-frontend && npm run lint && npm test && npm run build)
# manual: backend + worker + frontend; dos navegadores (customer y supervisor); guion docs/demo.md
```
