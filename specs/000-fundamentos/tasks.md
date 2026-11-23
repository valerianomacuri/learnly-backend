# Tasks: 00 · Fundamentos y convenciones

**Feature**: `000-fundamentos` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/00-fundamentos.md`

## Tareas

- [x] Test que verifica que cada rol recibe exactamente los permisos de la tabla.
- [x] Extender `Perm` / `ROLE_PERMISSIONS`; `ensure_rbac` ya los persiste (comprobar idempotencia).
- [x] Crear `domain/__init__.py` y un test (`tests/unit/test_architecture.py`) que falla si `app.domain` importa `sqlalchemy`, `fastapi` o `app.services`.
- [x] `Makefile` y docs.
