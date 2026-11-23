# ADR 0002 · Las tools de escritura no están al alcance del LLM

**Estado:** aceptada · **Specs:** 09, 10, 12 · **Challenge:** D4, D5, §6, §7

## Contexto
El agente conversa con clientes y, con un mensaje bien armado, puede ser inducido a actuar (prompt injection directa o a través de un chunk envenenado). Si el modelo puede invocar `create_refund`, una instrucción maliciosa o un malentendido produce un reembolso sin confirmación. Peor: si una tool recibe `customer_id`, el modelo puede ser llevado a actuar sobre otro cliente (BOLA).

## Decisión
- Hay una lista explícita `LLM_BOUND_TOOLS` en `agent/tools/registry.py` con solo las tools de **lectura** (`list_my_purchases`, `get_purchase_details`, `list_my_refunds`, `get_refund_status`). Es lo único que recibe `bind_tools`; el bucle ReAct de `refund_status` rechaza cualquier otro nombre (`TOOL_NOT_ALLOWED`).
- Las tools de escritura (`create_refund_request`, `cancel_refund_request`) las llaman **nodos del grafo**, con argumentos tomados del estado y solo después de un `interrupt()` con respuesta `confirm` del cliente. Cada una tiene una única arista de entrada (probado).
- La identidad **se inyecta**: la API pone `user_id`, `role`, `thread_id` y `request_id` en `config["configurable"]`; `SupportState` no tiene `customer_id`; ninguna tool declara `customer_id`/`role`/`user_id` como parámetro (un test falla si aparece, o si el registro gana una tool de aprobación, pago o notas).
- El agente no tiene permiso de aprobar: `AGENT_PERMISSIONS` es un subconjunto del RBAC del cliente más `refund:create`/`refund:cancel_own`, estos dos usados solo por los nodos de escritura.
- Los textos no confiables (mensajes, chunks) se envuelven en `<untrusted>` y pasan por guardrails de entrada y salida (spec 12).

## Alternativas consideradas
1. **Todas las tools enlazadas al LLM + prompt "no hagas X".** Un prompt no es un control de seguridad.
2. **Enlazar la escritura pero exigir un token de confirmación como argumento.** El modelo puede copiar el token de un turno anterior; el control seguiría dependiendo del modelo.
3. **Un agente por rol.** Más superficie, y no resuelve la inyección de identidad.

## Consecuencias
- (+) El peor caso con un LLM obediente es leer datos propios: el set adversarial (46 casos) verifica cero escrituras y cero fugas con un modelo que nunca se niega.
- (+) La confirmación humana es una propiedad del grafo, no del modelo.
- (−) Menos flexibilidad: el flujo de reembolso es un workflow híbrido con nodos fijos, no un agente libre.
- (−) Añadir una tool de escritura exige tocar el grafo y los tests de arquitectura (intencionalmente).
