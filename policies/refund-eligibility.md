---
doc_key: refund-eligibility
title: Elegibilidad para reembolsos
version: "2026-10-01"
effective_date: 2026-10-01
language: es
audience: customer
tags: [window, consumption, certificate, subscription, third-party, abuse]
supersedes: null
params:
  refund_window_days: 30
  max_progress_pct: 30
  abuse_flag_12m: 3
  abuse_block_12m: 5
  one_active_request_per_purchase: true
  rejected_is_final: true
reason_code_map:
  OUTSIDE_REFUND_WINDOW: "Elegibilidad > Ventana de 30 días"
  ALREADY_REFUNDED: "Elegibilidad > Una solicitud por compra"
  ACTIVE_REFUND_EXISTS: "Elegibilidad > Una solicitud por compra"
  PREVIOUSLY_REJECTED: "Elegibilidad > Una solicitud por compra"
  CONSUMPTION_LIMIT_EXCEEDED: "Elegibilidad > Consumo del curso"
  CERTIFICATE_ISSUED: "Elegibilidad > Certificado de finalización"
  SUBSCRIPTION_NOT_ELIGIBLE: "Elegibilidad > Compras no elegibles > Suscripciones"
  THIRD_PARTY_PURCHASE: "Elegibilidad > Compras no elegibles > Compras a terceros"
  REFUND_ABUSE_LIMIT: "Elegibilidad > Solicitudes frecuentes"
---

# Elegibilidad para reembolsos

Learnly quiere que aprendas sin riesgo. Los cursos elegibles comprados en Learnly pueden reembolsarse dentro de los 30 días posteriores a la compra, siempre que la solicitud cumpla las condiciones de esta política. Esta política está adaptada de la política pública de reembolsos de Udemy para fines de este proyecto; los umbrales marcados como "parámetro de la plataforma" no provienen de Udemy.

## Ventana de 30 días

Puedes solicitar el reembolso de un curso comprado en Learnly hasta 30 días después de la fecha y hora de la compra. El límite es inclusivo: una solicitud hecha exactamente a los 30 días todavía es válida.

La ventana se mide hasta el momento en que se crea la solicitud, no hasta el momento en que el supervisor la revisa. Si la solicitud se creó dentro de los 30 días y el supervisor la aprueba el día 35, sigue siendo válida.

Las solicitudes fuera de la ventana no se procesan. El motivo es que los pagos a los instructores se envían después de 30 días, por lo que no es posible revertirlos más tarde. Una solicitud fuera de plazo no se puede crear ni excepcionalmente.

## Consumo del curso

Un curso es elegible si has consumido como máximo el 30 % del contenido (parámetro de la plataforma). Si el progreso supera ese porcentaje, la solicitud no es elegible aunque estés dentro de los 30 días.

El progreso se calcula con las lecciones completadas registradas en tu cuenta.

## Certificado de finalización

Si ya obtuviste el certificado de finalización del curso, no es posible reembolsarlo, sin importar el tiempo transcurrido.

## Una solicitud por compra

- Solo puede existir una solicitud activa por compra. Una solicitud está activa mientras esté pendiente de aprobación, aprobada o procesando el pago.
- Una compra que ya fue reembolsada no puede reembolsarse de nuevo.
- Si cancelas tu solicitud antes de que se decida, puedes crear otra dentro de la ventana de 30 días.
- Si el supervisor rechazó una solicitud, la decisión es final para esa compra y no puede volver a solicitarse.

## Compras no elegibles

### Suscripciones

Los planes de suscripción comprados en Learnly no incluyen la garantía de 30 días. No se reembolsan cursos ni períodos de suscripción mediante este proceso.

### Compras a terceros

Si compraste el curso a través de un vendedor externo y no directamente en Learnly, esta política no aplica. Debes contactar al vendedor donde hiciste la compra.

## Solicitudes frecuentes

Para proteger a los instructores del abuso de la política, Learnly limita la cantidad de reembolsos (parámetros de la plataforma):

- Con 3 o más reembolsos completados en los últimos 12 meses, la solicitud se marca para revisión especial del supervisor, pero puede seguir su curso.
- Con 5 o más reembolsos completados en los últimos 12 meses, no se aceptan nuevas solicitudes.
