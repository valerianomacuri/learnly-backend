# Feature Specification: 12 · Seguridad, límites y resiliencia

**Feature**: `012-seguridad-resiliencia` · **Estado**: ✅ Implementada · **Migrada de**: `docs/specs/12-seguridad-resiliencia.md`

**Depende de:** 10 (y 11 para validar en UI) · **Refs:** challenge §2 (reglas del agente 5 y 6), §9 (autorización, injection, privacidad, resiliencia, límites). Usar el subagente `ai-engineer` para el set adversarial.

## Objetivo

Endurecer lo ya construido: límites de uso, guardrails reales, privacidad, suite BOLA/IDOR exhaustiva y degradación controlada cuando fallan LLM o vector store.

## Alcance

- Dentro: Redis, rate limit, presupuesto de tokens, guardrails de entrada/salida, PII, set adversarial, suite BOLA, circuit breaker, modelo de respaldo, degradación.
- Fuera: observabilidad/trazas (13).

## Criterios de aceptación

- 100 % de los intentos del set adversarial no producen escrituras ni fugas.
- 21.º mensaje del minuto → 429 con `Retry-After`; 6.º `POST /refunds` en una hora → 429.
- Con el LLM caído, el formulario completo (preview → crear) funciona.
- Una ruta nueva sin entrada en la tabla de permisos hace fallar la suite.
