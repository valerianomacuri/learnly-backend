# ADR 0007 · Vite + React en lugar de Next.js

**Estado:** aceptada · **Specs:** 06, 11 · **Challenge:** §1 (stack propuesto)

## Contexto
El challenge propone React/Next.js. El frontend es una aplicación autenticada tras login, con un chat por SSE y un dashboard en vivo: no necesita SEO ni renderizado en servidor, y toda la lógica de negocio vive en la API.

## Decisión
SPA con **Vite + React 19**, Tailwind, React Router y TanStack Query, en un repositorio aparte (`learnly-frontend`). Se sirve como estáticos (nginx en el compose) y habla con la API por `VITE_API_URL`.

## Alternativas consideradas
1. **Next.js.** Añade un segundo servidor Node, rutas y caché de servidor que aquí no aportan, y complica el SSE y la autenticación por token de la API.
2. **HTMX/plantillas en FastAPI.** Menos JavaScript, pero el chat con tarjeta HITL y el dashboard en vivo piden estado de cliente.

## Consecuencias
- (+) Despliegue trivial (archivos estáticos), arranque rápido, sin segundo runtime en producción.
- (−) Sin SSR: no sirve para páginas públicas indexables (fuera de alcance).
- (−) Se desvía del stack nominal del challenge; está registrado en la tabla de reconciliación del índice de specs.
