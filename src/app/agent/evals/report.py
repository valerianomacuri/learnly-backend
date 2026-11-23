"""Thresholds, console table and `reports/agent-evals-*.json|md` (spec 13)."""

import json
import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.agent.evals.runner import PROJECT_ROOT, RunResult

THRESHOLDS_PATH = PROJECT_ROOT / "evals" / "thresholds.toml"
REPORTS_DIR = PROJECT_ROOT / "reports"

LABELS = {
    "intent_accuracy": "Intención correcta",
    "task_completion": "Finalización de tarea",
    "unauthorized_tool_calls": "Tools no autorizadas",
}
MAX_METRICS = {"unauthorized_tool_calls"}  # lower is better; the rest are minimums


@dataclass(frozen=True, slots=True)
class Row:
    metric: str
    value: float
    threshold: float | None
    passed: bool | None


def load_thresholds(path: Path = THRESHOLDS_PATH) -> dict[str, float]:
    section = tomllib.loads(path.read_text(encoding="utf-8")).get("agent", {})
    return {k: float(v) for k, v in section.items()}


def evaluate_thresholds(
    summary: dict[str, float], thresholds: dict[str, float]
) -> list[Row]:
    rows: list[Row] = []
    for metric in LABELS:
        value, limit = summary[metric], thresholds.get(metric)
        if limit is None:
            rows.append(Row(metric, value, None, None))
        elif metric in MAX_METRICS:
            rows.append(Row(metric, value, limit, value <= limit))
        else:
            rows.append(Row(metric, value, limit, value >= limit))
    return rows


def failed(rows: list[Row]) -> list[Row]:
    return [r for r in rows if r.passed is False]


def _limit(row: Row) -> str:
    if row.threshold is None:
        return "-"
    sign = "<=" if row.metric in MAX_METRICS else ">="
    return f"{sign} {row.threshold:g}"


def _mark(row: Row) -> str:
    return "-" if row.passed is None else ("OK" if row.passed else "FALLA")


def console_table(result: RunResult, rows: list[Row]) -> str:
    lines = [
        f"Agent evals · mode={result.mode} · {len(result.results)} guiones",
        f"{'Métrica':<26}{'Valor':>8}{'Umbral':>9}  Estado",
    ]
    lines += [
        f"{LABELS[r.metric]:<26}{r.value:>8.3f}{_limit(r):>9}  {_mark(r)}" for r in rows
    ]
    misses = [r for r in result.results if not (r.intent_ok and r.completed)]
    for r in misses:
        lines.append(
            f"  x {r.id}: intent {r.intent} (esperado {r.expected_intent}), "
            f"resultado {r.outcome} (esperado {r.expected_outcome})"
        )
    return "\n".join(lines)


def markdown_report(result: RunResult, rows: list[Row]) -> str:
    out = [
        f"# Agent evals · {result.mode}",
        "",
        f"{len(result.results)} guiones.",
        "",
        "| Métrica | Valor | Umbral | Estado |",
        "| --- | --- | --- | --- |",
    ]
    out += [
        f"| {LABELS[r.metric]} | {r.value:.3f} | {_limit(r)} | {_mark(r)} |"
        for r in rows
    ]
    out += [
        "",
        "| Guion | Intención | Resultado | Tools llamadas |",
        "| --- | --- | --- | --- |",
    ]
    for r in result.results:
        ok_i = "ok" if r.intent_ok else f"x ({r.intent})"
        ok_o = "ok" if r.completed else f"x ({r.outcome})"
        out.append(
            f"| {r.id} | {ok_i} | {ok_o} {r.expected_outcome} | "
            f"{', '.join(r.tools_called) or '-'} |"
        )
    return "\n".join(out) + "\n"


def write_reports(result: RunResult, rows: list[Row]) -> tuple[Path, Path]:
    REPORTS_DIR.mkdir(exist_ok=True)
    stem = REPORTS_DIR / f"agent-evals-{date.today().isoformat()}-{result.mode}"
    json_path, md_path = stem.with_suffix(".json"), stem.with_suffix(".md")
    payload = result.as_dict()
    payload["thresholds"] = [
        {
            "metric": r.metric,
            "value": r.value,
            "threshold": r.threshold,
            "passed": r.passed,
        }
        for r in rows
    ]
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    md_path.write_text(markdown_report(result, rows), encoding="utf-8")
    return json_path, md_path
