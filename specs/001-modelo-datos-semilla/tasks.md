# Tasks: 01 · Modelo de datos, semilla y lectura de compras

**Feature**: `001-modelo-datos-semilla` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/01-modelo-datos-semilla.md`

## Tareas

- [x] Enums de dominio + modelos + `models/__init__.py`.
- [x] Migración `0002` (autogenerate, revisar a mano; trigger y índice parcial a mano). `alembic upgrade head` desde BD vacía y `alembic check`.
- [x] Test de BD: trigger rechaza UPDATE y DELETE en `refund_events`; índice parcial rechaza dos solicitudes activas para la misma compra pero permite una nueva tras `CANCELLED`.
- [x] Repos/servicios/rutas de compras; tests de cursor, filtros y **BOLA** (customer A pide compra de B → 404, idéntico al inexistente).
- [x] Fixtures de dominio en `tests/conftest.py` (`make_course`, `make_purchase(days_ago=, progress=, ...)`).
- [x] Semilla y `tests/test_seed.py` ampliado.
