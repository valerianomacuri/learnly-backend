"""Thresholds, console table and `reports/rag-evals-*.json|md` (spec 08)."""

import json
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.rag.evals import metrics
from app.rag.evals.runner import RunResult

PROJECT_ROOT = Path(__file__).resolve().parents[4]
THRESHOLDS_PATH = PROJECT_ROOT / "evals" / "thresholds.toml"
REPORTS_DIR = PROJECT_ROOT / "reports"

LABELS = {
    "recall_at_5": "Recall@5",
    "mrr": "MRR",
    "precision_at_5": "Precisión@5 (contexto)",
    "faithfulness": "Faithfulness",
    "citation_precision": "Precisión de citas",
    "context_precision": "Context precision",
    "context_recall": "Context recall",
    "answer_relevancy": "Answer relevancy",
    "correct_abstention": "Abstención correcta",
    "correct_figures": "Cifras correctas",
    "latency_p50_s": "Latencia p50 (s)",
    "latency_p95_s": "Latencia p95 (s)",
    "tokens_est_total": "Tokens estimados",
    "poisoned_leaks": "Fugas doc. envenenado",
}
# Metrics that are reported but have no threshold.
INFORMATIONAL = (
    "mrr",
    "precision_at_5",
    "context_precision",
    "context_recall",
    "answer_relevancy",
    "latency_p50_s",
    "tokens_est_total",
)


@dataclass(frozen=True, slots=True)
class Row:
    metric: str
    value: float | None
    threshold: float | None
    passed: bool | None  # None: not measured / not gated


def load_thresholds(mode: str, path: Path = THRESHOLDS_PATH) -> dict[str, float]:
    section = tomllib.loads(path.read_text(encoding="utf-8")).get(mode, {})
    return {k: float(v) for k, v in section.items()}


def evaluate_thresholds(
    summary: dict[str, float | None], thresholds: dict[str, float]
) -> list[Row]:
    rows: list[Row] = []
    for metric in LABELS:
        value = summary.get(metric)
        limit = thresholds.get(metric)
        if limit is None:
            rows.append(Row(metric, value, None, None))
        elif metric == "poisoned_leaks":
            rows.append(Row(metric, value, limit, value is not None and value <= limit))
        else:
            rows.append(
                Row(metric, value, limit, metrics.check_threshold(metric, value, limit))
            )
    return rows


def failed(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.passed is False]


def _fmt(metric: str, value: float | None) -> str:
    if value is None:
        return "n/a"
    if metric == "tokens_est_total" or metric == "poisoned_leaks":
        return f"{value:.0f}"
    if metric.endswith("_s"):
        return f"{value:.2f}"
    return f"{value:.3f}"


def _mark(row: Row) -> str:
    if row.passed is None:
        return "-"
    return "OK" if row.passed else "FALLA"


def _limit(row: Row) -> str:
    if row.threshold is None:
        return "-"
    if row.metric == "poisoned_leaks":
        return f"<= {row.threshold:.0f}"
    sign = "<" if metrics.DIRECTIONS[row.metric] == "max" else ">="
    return f"{sign} {row.threshold:g}"


def console_table(result: RunResult, rows: list[Row]) -> str:
    lines = [
        f"RAG evals · strategy={result.strategy} · mode={result.mode} · "
        f"{len(result.cases)} preguntas · {result.chunks} chunks",
        f"{'Métrica':<24}{'Valor':>10}{'Umbral':>10}  Estado",
    ]
    for row in rows:
        lines.append(
            f"{LABELS[row.metric]:<24}{_fmt(row.metric, row.value):>10}"
            f"{_limit(row):>10}  {_mark(row)}"
        )
    return "\n".join(lines)


def markdown_report(result: RunResult, rows: list[Row]) -> str:
    out = [
        f"# RAG evals · {result.strategy} · {result.mode}",
        "",
        f"{len(result.cases)} preguntas, {result.chunks} chunks.",
        "",
        "| Métrica | Valor | Umbral | Estado |",
        "| --- | --- | --- | --- |",
    ]
    out += [
        f"| {LABELS[r.metric]} | {_fmt(r.metric, r.value)} | {_limit(r)} | {_mark(r)} |"
        for r in rows
    ]
    misses = [c for c in result.cases if c.recall is not None and c.recall < 1.0]
    if misses:
        out += ["", "## Preguntas sin recuperar la sección esperada", ""]
        out += [
            f"- `{c.id}` {c.question} → {', '.join(c.retrieved[:3])}" for c in misses
        ]
    wrong = [c for c in result.cases if not c.abstention_ok]
    if wrong:
        out += ["", "## Abstención incorrecta", ""]
        out += [
            f"- `{c.id}` {c.question} (esperado abstenerse: {c.should_abstain})"
            for c in wrong
        ]
    figures = [c for c in result.cases if c.unsupported_figures]
    if figures:
        out += ["", "## Cifras no respaldadas", ""]
        out += [
            f"- `{c.id}` {c.unsupported_figures}: {c.answer[:120]}" for c in figures
        ]
    leaks = [p for p in result.poisoned if p.leaked]
    if leaks:
        out += ["", "## Fugas del documento envenenado", ""]
        out += [f"- {p.question}: {p.answer[:120]}" for p in leaks]
    return "\n".join(out) + "\n"


def write_reports(
    result: RunResult, rows: list[Row], directory: Path = REPORTS_DIR
) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"rag-evals-{date.today().isoformat()}-{result.strategy}-{result.mode}"
    payload = result.as_dict() | {
        "thresholds": [
            {
                "metric": r.metric,
                "value": r.value,
                "threshold": r.threshold,
                "passed": r.passed,
            }
            for r in rows
        ]
    }
    json_path = directory / f"{stem}.json"
    md_path = directory / f"{stem}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_path.write_text(markdown_report(result, rows), encoding="utf-8")
    return json_path, md_path
