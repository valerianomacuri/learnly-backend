# Demo sin LLM: preview → crear → aprobar → worker → completado

Todo con la API y el worker; no interviene ningún LLM.

```bash
docker compose up -d db
uv run alembic upgrade head
uv run seed --no-tokens
uv run uvicorn app.main:app --app-dir src --loop asyncio:SelectorEventLoop &

API=localhost:8000/api/v1
login() { curl -s $API/auth/login -H 'content-type: application/json' \
  -d "{\"email\":\"$1\",\"password\":\"seed-password-1\"}" | python -c 'import sys,json;print(json.load(sys.stdin)["access_token"])'; }
ANN=$(login ann@example.com)
SUP=$(login sup1@support.example)

# 1. Una compra elegible de ann (ver `uv run seed` / GET /purchases)
PURCHASE=<purchase_id>
BODY='{"purchase_id":"'$PURCHASE'","reason_category":"NOT_AS_DESCRIBED","reason_text":"The course content does not match its description."}'

# 2. Preview -> confirmation_token
TOKEN=$(curl -s -X POST $API/refunds/preview -H "Authorization: Bearer $ANN" \
  -H 'content-type: application/json' -d "$BODY" | python -c 'import sys,json;print(json.load(sys.stdin)["confirmation_token"])')

# 3. Crear la solicitud
RID=$(curl -s -X POST $API/refunds -H "Authorization: Bearer $ANN" \
  -H 'content-type: application/json' -H "Idempotency-Key: demo-$RANDOM" \
  -d "${BODY%\}},\"confirmation_token\":\"$TOKEN\"}" | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 4. Aprobar (escribe refund_payouts + outbox en la misma transacción)
curl -s -X POST $API/supervisor/refunds/$RID/approve -H "Authorization: Bearer $SUP" -H 'If-Match: 1'

# 5. El worker paga y completa
uv run payouts-worker --once

# 6. COMPLETED y payout SUCCEEDED
curl -s $API/refunds/$RID -H "Authorization: Bearer $ANN"
```

Fallos: con `FAIL_RATE=1 uv run payouts-worker --once` el reembolso pasa a `PROCESSING`
y se reintenta con backoff (`PAYOUT_BACKOFF_BASE_SECONDS`); agotados los intentos queda
`FAILED` y un supervisor lo reintenta con
`POST /supervisor/refunds/$RID/retry-payout` (202) seguido de otra pasada del worker.

## Recorrido en la UI (spec 06)

Mismo flujo, sin `curl`. Requiere backend, worker y frontend:

```bash
uv run uvicorn app.main:app --app-dir src --loop asyncio:SelectorEventLoop   # :8000
uv run payouts-worker                                                      # proceso aparte
(cd ../learnly-frontend && npm i && npm run dev)                           # http://localhost:5173
```

Contraseña de todos los usuarios semilla: `seed-password-1`.

1. **Customer** (`ann@example.com`): `/purchases` muestra los días restantes de cada compra →
   "Solicitar reembolso" → categoría + razón (20–1000 caracteres) → **Ver elegibilidad**.
   Si es inelegible se listan los motivos traducidos y las citas; si es elegible se abre el modal
   de confirmación (monto, método, razón, fecha límite) → **Confirmar solicitud**. La
   `Idempotency-Key` se genera al abrir el modal y se reutiliza en los reintentos.
2. **Supervisor** (`sup1@support.example`, otra ventana): `/supervisor` muestra la solicitud al
   instante (indicador "En vivo", SSE por `fetch` con Bearer). Abre el detalle, revisa
   elegibilidad, compra e historial, y **Aprobar** (nota opcional) o **Rechazar** (nota ≥ 10).
3. Con el worker en marcha, en `/refunds` del customer el estado pasa a `COMPLETED`
   (se refresca solo mientras haya solicitudes en curso). El seguimiento público se ve en
   "Ver seguimiento".
4. **Conflicto**: abre el mismo detalle en dos pestañas de supervisor; la segunda en decidir ve
   "Otro supervisor ya decidió esta solicitud" y el detalle se recarga.
5. **Pago fallido**: con `FAIL_RATE=1` el detalle muestra *Reintentar pago* cuando queda `FAILED`.
6. `/policies` (ambos roles): visor de las políticas ACTIVE con su versión y fecha.
