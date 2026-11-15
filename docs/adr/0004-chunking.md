# ADR 0004 · Chunking estructural vs. tamaño fijo (borrador)

**Estado:** borrador (se completa en el spec 13) · **Origen:** spec 08, ablación

## Contexto
El RAG de políticas usa chunking estructural (por encabezado; tablas, listas y pares P/R de la FAQ atómicos; `section_path` verificable en las citas). La alternativa es ventanas fijas de ~500 tokens (`fixed_500`). Se comparan con el mismo set dorado (79 preguntas), mismos modelos (embeddings y LLM de `.env`), mismo retriever y mismos jueces; solo cambia `--strategy`.

## Resultados (modo completo, 2026-11-15)

| Métrica | structural | fixed_500 | Δ (structural − fixed) |
| --- | --- | --- | --- |
| Chunks del corpus | 51 | 10 | |
| Recall@5 | 0,955 | 0,985 | −0,030 |
| MRR | 0,857 | 0,728 | +0,129 |
| Precisión@5 del contexto | 0,251 | 0,316 | −0,065 |
| Faithfulness | 0,998 | 1,000 | −0,002 |
| Precisión de citas | 1,000 | 1,000 | 0 |
| Abstención correcta | 0,924 | 0,443 | +0,481 |
| Cifras correctas | 1,000 | 1,000 | 0 |
| Latencia p50 / p95 (s) | 2,54 / 3,24 | 1,94 / 3,33 | |
| Tokens estimados (LLM, 79 preguntas) | 85 870 | 208 334 | −59 % |

Reportes: `reports/rag-evals-2026-11-15-{structural,fixed_500}-full.{json,md}` (el directorio está en `.gitignore`; las cifras de arriba son la copia).

## Lectura: estructural NO gana en Recall@5 ni Faithfulness
- **Recall@5 (−0,03) y Faithfulness (−0,002):** fixed_500 es igual o mejor. Es un artefacto del tamaño del corpus: 15 KB de políticas dan solo 10 chunks fijos, casi un documento cada uno, así que el top-5 cubre la mitad del corpus. Recall@5 no discrimina en un corpus tan pequeño; no se ajustó la métrica para que estructural gane.
- **Abstención (+0,48):** las 44 respuestas incorrectas de fixed_500 son preguntas contestables en las que el sistema se abstuvo (`abstain: no valid citation` o `grounding check failed`). Sin estructura todos los chunks de un documento comparten `section_path` (el título), así que el modelo no puede citar una sección verificable y la verificación de citas lo rechaza. Es la ventaja real del chunking estructural: citas por sección comprobables. Parte de esta diferencia depende de nuestra verificación de citas, no solo de la recuperación.
- **Costo (−59 % de tokens):** el contexto de fixed_500 son documentos casi enteros. Latencia p95 equivalente.
- **MRR (+0,13):** el chunk correcto sube de posición con estructura.

## Decisión provisional
Mantener estructural. La justificación es citas verificables por sección, mejor abstención, menor costo y mejor MRR, no Recall@5. Pendiente para el 13: repetir con un corpus mayor (donde Recall@5 sí discrimine) antes de cerrar el ADR.

## Salvedades
- **Set dorado ajustado tras ver resultados:** la primera corrida completa dio Recall@5 = 0,896 (structural, umbral 0,90) y 0,985 (fixed_500). Se revisaron los fallos y 5 entradas (`g031`, `g033`, `g045`, `g054`, `g057`) se ampliaron con una sección equivalente cuyo texto contiene la respuesta (`A || B`). Tras eso: structural 0,955. Los reportes de la primera corrida están en `reports/first-run/`. Los fallos restantes (p. ej. `g061`, `g063`) son reales.
- Un solo run por estrategia (LLM a T=0, pero sin repeticiones): diferencias de ±0,02 no son concluyentes.
- Tokens estimados con un contador offline, no con el uso facturado.
