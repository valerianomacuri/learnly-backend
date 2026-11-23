# ADR 0006 · `refund_events` es la única auditoría

**Estado:** aceptada · **Specs:** 01, 03, 13 · **Challenge:** §9 Auditoría

## Contexto
Hace falta un registro de quién hizo qué sobre cada solicitud, que no se pueda reescribir. La tentación es una tabla genérica `audit_log` para todo (login, usuarios, reembolsos).

## Decisión
- `refund_events` es **append-only** (un trigger de base de datos rechaza `UPDATE` y `DELETE`; definido en el modelo para que `create_all` lo cree y a mano en la migración 0002) y es la **única** auditoría. Cada fila lleva `type`, `actor_type`, `actor_id` y `payload` con el `trace_id` de la petición.
- `RefundEventService.append` es la única vía de escritura. Solo los tipos en `PUBLIC_EVENT_TYPES` llegan a la línea de tiempo del cliente; `payload` jamás se serializa en esquemas de cliente.
- No se reintroduce una tabla de auditoría genérica; el login no se audita en base de datos (los intentos fallidos se devuelven, no se lanzan, y quedan en los logs JSON).
- La escalada a humano no tiene solicitud asociada, así que va al log estructurado `app.agent.audit` con el `trace_id`.

## Alternativas consideradas
1. **`audit_log` genérico.** Mezcla dominios con permisos distintos y duplica la fuente de verdad del ciclo de vida.
2. **Event sourcing completo.** Sobredimensionado: el estado actual se guarda en `refund_requests` y los eventos lo explican.

## Consecuencias
- (+) Una sola historia por solicitud, inmutable a nivel de base de datos, con correlación a logs y trazas.
- (−) Lo que no es un reembolso (usuarios, login, escalada sin solicitud) no tiene auditoría en base de datos.
