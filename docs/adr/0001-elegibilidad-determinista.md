# ADR 0001 · La elegibilidad la decide código determinista, no el LLM

**Estado:** aceptada · **Specs:** 02, 03 · **Challenge:** D1, D2, D6, BR-01…BR-10

## Contexto
Un reembolso mueve dinero. La pregunta "¿esta compra es reembolsable?" depende de reglas precisas (plazo de 30 días, tope de consumo, certificado, suscripciones, compras a terceros, historial de abuso). Un LLM puede alucinar un plazo, aceptar una excepción que el cliente le "negocia" o cambiar de criterio entre dos ejecuciones. Además la decisión debe poder auditarse y reproducirse meses después.

## Decisión
- `domain/eligibility.py` contiene `RefundPolicyEngine`: una función pura (sin SQLAlchemy, FastAPI ni red) con reloj inyectado que evalúa BR-01…BR-10, **acumula todos** los `reason_codes` y devuelve monto y método de reembolso.
- Los umbrales (30 días, 30 % de consumo, 3/5 solicitudes en 12 meses) **no son settings**: salen del front-matter YAML de los mismos documentos de política (`policies/*.md`), la única fuente de verdad. `PolicyRepository.get_active_config()` construye un `PolicyConfig` inmutable.
- El LLM solo *explica*: narra el resultado y cita la sección con `reason_code_map` (citas deterministas, sin embeddings). No tiene ninguna forma de cambiar el veredicto.
- Un intento inelegible no crea solicitud: responde 422 con `reason_codes` y deja una fila en `eligibility_checks` (auditoría sin ensuciar la cola del supervisor).
- `tests/unit/test_architecture.py` falla si `domain/` importa SQLAlchemy, FastAPI o servicios.

## Alternativas consideradas
1. **LLM con las políticas en el prompt.** Menos código, pero no determinista ni testeable en los bordes (30 días menos 1 s); un prompt injection podría mover la decisión.
2. **Reglas en el prompt y verificación en código.** Duplica la lógica y deja dos fuentes de verdad que divergen.
3. **Umbrales en variables de entorno.** Fácil de cambiar, pero se desacoplan del texto que ve el cliente: el documento diría 30 días y el código 45.

## Consecuencias
- (+) Pruebas con Hypothesis en los bordes; la misma decisión desde chat y formulario; reproducible con `policy_version`.
- (+) Cambiar un umbral es editar el documento y volver a cargarlo (`policies-load`); ver ADR 0005.
- (−) Una regla nueva requiere código y despliegue, no solo un cambio de prompt.
- (−) El agente no puede "ser flexible" con casos límite: deriva a una persona (handoff).
