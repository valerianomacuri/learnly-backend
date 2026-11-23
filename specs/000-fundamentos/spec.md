# Feature Specification: 00 · Fundamentos y convenciones

**Feature**: `000-fundamentos` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/00-fundamentos.md`

**Depende de:** nada · **Refs:** challenge §1, §2, §10

## Objetivo

Dejar fijadas las convenciones y el RBAC del dominio antes de escribir entidades, de modo que los specs siguientes no discutan estructura.

## Alcance

- Dentro: extensión del catálogo de permisos, `Makefile`, paquetes vacíos con su regla de dependencia, actualización de `CLAUDE.md`/README.
- Fuera: cualquier tabla o endpoint de negocio (spec 01).

## Criterios de aceptación

- `ensure_rbac` dos veces no duplica filas.
- El test de arquitectura falla si se añade un import prohibido en `domain/`.
- Un supervisor **no** tiene `refund:create` ni `chat:use` (BR-11).
