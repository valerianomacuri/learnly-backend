# ADR 0004 · Chunking estructural vs. tamaño fijo

**Estado:** aceptada (spec 13) · **Origen:** spec 07 (pipeline), spec 08 (ablación)

## Contexto
El RAG de políticas usa chunking estructural (por encabezado; tablas, listas y pares P/R de la FAQ atómicos; `section_path` verificable en las citas). La alternativa es ventanas fijas de ~500 tokens (`fixed_500`). Se comparan con el mismo set dorado (79 preguntas), mismos modelos (embeddings y LLM de `.env`), mismo retriever y mismo juez (RAGAS); solo cambia `--strategy`.

## Resultados (modo completo, jueces RAGAS, 2026-11-24)

Faithfulness y precisión de citas las calcula RAGAS (spec 08 migrada); antes eran jueces LLM propios (corrida del 2026-11-15: Faithfulness 0,998 / 1,000 y citas 1,000 / 1,000, **no comparables** con las de abajo).

| Métrica | structural | fixed_500 | Δ (structural − fixed) |
| --- | --- | --- | --- |
| Chunks del corpus | 51 | 10 | |
| Recall@5 | 0,955 | 0,985 | −0,030 |
| MRR | 0,857 | 0,728 | +0,129 |
| Precisión@5 del contexto (léxica) | 0,251 | 0,316 | −0,065 |
| Faithfulness (RAGAS) | 0,881 | 0,934 | −0,053 |
| Precisión de citas (RAGAS por cita) | 0,853 | 0,896 | −0,043 |
| Context precision (RAGAS) | 0,870 | 0,880 | −0,010 |
| Context recall (RAGAS) | 0,983 | 1,000 | −0,017 |
| Answer relevancy (RAGAS) | 0,700 | 0,759 | −0,059 |
| Abstención correcta | 0,899 | 0,430 | +0,469 |
| Cifras correctas | 1,000 | 1,000 | 0 |
| Latencia p50 / p95 (s) | 2,66 / 3,58 | 2,16 / 3,62 | |
| Tokens estimados (LLM, 79 preguntas) | 87 716 | 219 346 | −60 % |

Reportes: `reports/rag-evals-2026-11-24-{structural,fixed_500}-full.{json,md}` (el directorio está en `.gitignore`; las cifras de arriba son la copia). 0 métricas RAGAS fallidas en ambas corridas.

## Lectura: estructural NO gana en las métricas de recuperación ni de generación de RAGAS
- **Recall@5 (−0,03), Faithfulness (−0,05), citas (−0,04), context precision/recall y answer relevancy:** fixed_500 es igual o mejor en todas. Parte es artefacto del tamaño del corpus: 15 KB de políticas dan solo 10 chunks fijos, casi un documento cada uno, así que el top-5 cubre la mitad del corpus y Recall@5 / context recall no discriminan. No se ajustó ninguna métrica para que estructural gane.
- **Faithfulness de RAGAS es más estricta que el juez anterior** (0,881 vs 0,998 con la misma estrategia). Los casos más bajos (`g010`, `g011`, `g013`) son respuestas correctas y respaldadas por la política cuyas frases sobre la situación del usuario ("has consumido más del 30 %", "ya obtuviste el certificado") vienen de la pregunta y no del contexto; RAGAS las cuenta como no respaldadas. Con fixed_500 el contexto son documentos casi enteros, con más texto que puede respaldar esas frases, lo que explica parte de la diferencia a su favor.
- **Abstención (+0,47):** las respuestas incorrectas de fixed_500 son preguntas contestables en las que el sistema se abstuvo (`abstain: no valid citation` o `grounding check failed`). Sin estructura todos los chunks de un documento comparten `section_path` (el título), así que el modelo no puede citar una sección verificable y la verificación de citas lo rechaza. Es la ventaja real del chunking estructural: citas por sección comprobables. Parte de esta diferencia depende de nuestra verificación de citas, no solo de la recuperación.
- **Costo (−60 % de tokens):** el contexto de fixed_500 son documentos casi enteros. Latencia p95 equivalente.
- **MRR (+0,13):** el chunk correcto sube de posición con estructura.

## Decisión
Chunking **estructural** (por encabezado; tablas, listas y pares P/R de la FAQ atómicos; `section_path` verificable). La justificación es citas verificables por sección, mejor abstención, menor costo y mejor MRR, **no** Recall@5 ni las métricas de generación de RAGAS, donde fixed_500 es igual o ligeramente mejor.

## Alternativas
1. **`fixed_500`** (ventanas de ~500 tokens). Más simple y con Recall@5 igual o mejor aquí, pero sin `section_path` propio: la verificación de citas rechaza respuestas correctas (abstención 0,43 vs 0,90) y el contexto cuesta 2,4× más tokens.
2. **Un chunk por documento.** Peor precisión de contexto y citas solo a nivel de documento.
3. **Semántico (por similitud entre frases).** No se probó: añade un modelo al camino de ingesta y no garantiza citas por sección.

## Consecuencias
- (+) Cada cita apunta a una sección real y comprobable; las respuestas ajenas a la política se abstienen mejor.
- (−) Más chunks (51 vs 10) y dependencia de que los documentos tengan estructura de encabezados (`policies/*.md` la tiene; un PDF plano no).
- (−) **Límite de la evidencia:** el corpus es de ~15 KB, así que Recall@5 no discrimina y esta ablación no demuestra que estructural recupere mejor a escala. Con un corpus mayor habría que repetirla; el comando es `uv run python -m app.rag.evals.run --strategy fixed_500` (y `structural`). No se repitió en el spec 13 por no contar con un corpus mayor.

## Salvedades
- **Set dorado ajustado tras ver resultados:** la primera corrida completa dio Recall@5 = 0,896 (structural, umbral 0,90) y 0,985 (fixed_500). Se revisaron los fallos y 5 entradas (`g031`, `g033`, `g045`, `g054`, `g057`) se ampliaron con una sección equivalente cuyo texto contiene la respuesta (`A || B`). Tras eso: structural 0,955 (misma corrida con RAGAS). Los reportes de la primera corrida están en `reports/first-run/`. Los fallos restantes (p. ej. `g061`, `g063`) son reales.
- Un solo run por estrategia (LLM a T=0, pero sin repeticiones): diferencias de ±0,02 no son concluyentes.
- Tokens estimados con un contador offline, no con el uso facturado.
