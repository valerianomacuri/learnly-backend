# 00 · Fundamentos y convenciones

**Depende de:** nada · **Refs:** challenge §1, §2, §10

## Objetivo
Dejar fijadas las convenciones y el RBAC del dominio antes de escribir entidades, de modo que los specs siguientes no discutan estructura.

## Alcance
- Dentro: extensión del catálogo de permisos, `Makefile`, paquetes vacíos con su regla de dependencia, actualización de `CLAUDE.md`/README.
- Fuera: cualquier tabla o endpoint de negocio (spec 01).

## Cambios
**Permisos** (`src/app/core/permissions.py`): añadir a `Perm` y `ROLE_PERMISSIONS` según la matriz del challenge §2.

| Permiso | customer | supervisor |
| --- | --- | --- |
| `purchase:read_own` | ✅ | |
| `purchase:read_for_review` (solo la compra de la solicitud que revisa) | | ✅ |
| `policy:read` | ✅ | ✅ |
| `refund:simulate` | ✅ | |
| `refund:create` | ✅ | |
| `refund:read_own` | ✅ | |
| `refund:read_any` | | ✅ |
| `refund:cancel_own` | ✅ | |
| `refund:decide` | | ✅ |
| `refund:retry_payout` | | ✅ |
| `note:write`, `note:read` | | ✅ |
| `chat:use` | ✅ | |

Los permisos del agente son un **subconjunto** de los del customer (`Principal.acting_via == "agent"`, spec 09); no se crea rol "agent".

**Estructura**: crear `src/app/domain/`, y reservar `agent/`, `rag/`, `workers/` (se crean en su spec). Regla: `domain/` no importa SQLAlchemy, FastAPI ni `services/`.

**`Makefile`**: `install`, `db`, `migrate`, `seed`, `run`, `test`, `lint` (ruff check + format --check), `typecheck` (mypy), `evals` (placeholder que se llena en 08/13), `check` (todo lo anterior).

**Settings** (`core/config.py`): añadir, con defaults, los parámetros que se usarán luego sin cambiar contratos: `CONFIRMATION_SECRET` (por defecto derivado de `JWT_SECRET`), `CONFIRMATION_TTL_MIN` (10), `SLA_HOURS` (48). Los umbrales de política **no** van aquí: salen del front-matter (spec 02).

**Docs**: actualizar `CLAUDE.md` (sección Architecture: paquetes nuevos, `refund_events` como única auditoría, convención de specs) y enlazar `docs/specs/README.md` desde el README.

## Tareas
- [x] Test que verifica que cada rol recibe exactamente los permisos de la tabla.
- [x] Extender `Perm` / `ROLE_PERMISSIONS`; `ensure_rbac` ya los persiste (comprobar idempotencia).
- [x] Crear `domain/__init__.py` y un test (`tests/unit/test_architecture.py`) que falla si `app.domain` importa `sqlalchemy`, `fastapi` o `app.services`.
- [x] `Makefile` y docs.

## Criterios de aceptación
- `ensure_rbac` dos veces no duplica filas.
- El test de arquitectura falla si se añade un import prohibido en `domain/`.
- Un supervisor **no** tiene `refund:create` ni `chat:use` (BR-11).

## Verificación
```bash
make check
uv run pytest tests/test_authz.py tests/unit -q
```
