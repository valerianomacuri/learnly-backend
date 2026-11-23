# Tasks: 04 · API del supervisor

**Feature**: `004-supervisor-api` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/04-supervisor-api.md`

## Tareas

- [x] Tests primero: matriz de permisos (customer → 403 en todas las rutas `/supervisor/*`), self-approval, rechazo sin nota/nota corta, re-decisión → 409, `If-Match` viejo → 409.
- [x] **Concurrencia:** dos supervisores aprueban a la vez (sesiones separadas, `asyncio.gather`) → un 200 y un 409.
- [x] Servicio, rutas, schemas (`SupervisorRefundRead` ≠ `RefundRead`), helper de enmascarado.
- [x] Test: las notas nunca aparecen en `GET /refunds/{id}`, `/events` del customer ni en ningún schema de customer (test que serializa todos los schemas de customer y busca el texto de la nota).
- [x] Filtros y cursor con datos de la semilla; test de `sla_breached` con reloj inyectado.
- [x] Ampliar la semilla: solicitudes en distintos estados/edades para que el dashboard tenga datos.
