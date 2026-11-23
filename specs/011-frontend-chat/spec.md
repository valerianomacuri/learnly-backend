# Feature Specification: 11 · Frontend del chat con tarjeta HITL

**Feature**: `011-frontend-chat` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/11-frontend-chat.md`

**Depende de:** 06, 10 · **Refs:** challenge §9 (Frontend mínimo: chat), §6 (payload del interrupt) · **Hito: flujo completo con chat**
**Código en:** `../learnly-frontend`

## Objetivo

Widget de chat para el customer con respuestas en streaming y tarjeta de confirmación (Confirmar / Editar razón / Cancelar) que sobrevive a una recarga.

## Alcance

- Dentro: página `/chat`, cliente SSE (`fetch` + `ReadableStream`, reutiliza el del spec 06), tarjeta HITL, historial, recuperación de interrupt.
- Fuera: reconexión con `Last-Event-ID` (extensión opcional).

## Criterios de aceptación

- Chat → "quiero un reembolso de <curso>" → pregunta de razón → tarjeta → Confirmar → mensaje de éxito → la solicitud aparece en vivo en el dashboard del supervisor con canal `CHAT`.
- Recargar la página con la tarjeta visible la restaura idéntica.
- Con el backend de LLM caído el chat muestra el error y ofrece el formulario; el formulario sigue funcionando.
- `npm run lint && npm test && npm run build` en verde.
