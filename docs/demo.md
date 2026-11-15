# Demo: chat con confirmación HITL (spec 11)

Requiere `OPENAI_API_KEY` en el backend (sin ella `/chat` responde 503 y la UI ofrece el formulario).

## Preparación
```bash
docker compose up -d db
uv run alembic upgrade head && uv run seed          # imprime los JWT / credenciales
uv run uvicorn app.main:app --reload --app-dir src   # Windows sin --reload: --loop asyncio:SelectorEventLoop
uv run payouts-worker                                # otra terminal
(cd ../learnly-frontend && npm run dev)              # http://localhost:5173
```
Dos navegadores (o uno normal y uno privado): **customer** (`ann`) y **supervisor**.

## Recorrido
1. Supervisor: abre **Cola** (indicador "en vivo").
2. Customer: **Chat → Nueva conversación** → "quiero un reembolso de *<curso>*".
3. El agente pide la razón (≥ 20 caracteres) → aparece la **tarjeta de confirmación** (monto, método, razón, fecha límite, citas, contador de expiración).
4. Recarga la página (F5): la tarjeta se restaura idéntica y el compositor sigue deshabilitado.
5. Prueba **Editar razón** (20–1000 caracteres) y luego **Confirmar** → mensaje de éxito.
6. Supervisor: la solicitud aparece en vivo con canal `CHAT`; aprobarla → el worker la procesa → el customer ve `COMPLETED` en **Mis solicitudes**.

## Casos límite
- Con la tarjeta visible, escribe en el chat desde otra pestaña: 409 `INTERRUPT_PENDING`, la tarjeta se resalta y el texto no se pierde.
- Deja expirar la tarjeta: los botones se deshabilitan y **Reiniciar confirmación** genera una tarjeta nueva.
- Detén el backend de LLM / quita `OPENAI_API_KEY`: el chat muestra el error con **Reintentar** y un enlace al formulario (`/refunds/new`), que sigue funcionando.
- Más de N mensajes por minuto: aviso 429 con el `Retry-After`.
