import pytest

from app.rag.evals import metrics, report

SECTION = (
    "Puedes solicitar el reembolso hasta 30 días después de la compra. "
    "El límite es inclusivo y se mide hasta la creación de la solicitud."
)


def test_recall_is_the_fraction_of_expected_sections_found() -> None:
    assert metrics.recall_at_k([True, False], 2) == 0.5
    assert metrics.recall_at_k([True], 1) == 1.0
    with pytest.raises(ValueError):
        metrics.recall_at_k([], 0)


def test_reciprocal_rank_uses_the_first_covering_chunk() -> None:
    assert metrics.reciprocal_rank([False, False, True, True]) == pytest.approx(1 / 3)
    assert metrics.reciprocal_rank([True]) == 1.0
    assert metrics.reciprocal_rank([False, False]) == 0.0
    assert metrics.reciprocal_rank([]) == 0.0


def test_precision_at_k() -> None:
    assert metrics.precision_at_k([True, False, False, True]) == 0.5
    assert metrics.precision_at_k([]) == 0.0


def test_covers_by_section_path() -> None:
    assert metrics.covers("Ventana", "otro texto", "Ventana", SECTION)
    assert not metrics.covers("Consumo", "otro texto", "Ventana", SECTION)


def test_covers_by_content_when_the_structure_is_lost() -> None:
    whole = f"# Elegibilidad\n\n## Ventana\n\n{SECTION}\n\n## Consumo\n\nOtra cosa."
    assert metrics.covers("Elegibilidad", whole, "Ventana", SECTION)
    half = "El límite es inclusivo y se mide hasta la creación de la solicitud."
    assert metrics.covers("Elegibilidad", half, "Ventana", SECTION)
    assert not metrics.covers(
        "Elegibilidad", "Texto sin relación alguna.", "Ventana", SECTION
    )


@pytest.mark.parametrize(
    ("should", "handoff", "ok"),
    [
        (True, True, True),
        (False, False, True),
        (True, False, False),
        (False, True, False),
    ],
)
def test_abstention_correct_is_symmetric(should: bool, handoff: bool, ok: bool) -> None:
    assert metrics.abstention_correct(should, handoff) is ok


def test_figures_extracts_days_and_percentages() -> None:
    assert metrics.figures("30 días, el 30 % y 2,5 days; 10 días hábiles") == {
        "30",
        "2.5",
        "10",
    }
    assert metrics.figures("sin cifras") == set()


def test_unsupported_figures() -> None:
    assert metrics.unsupported_figures("Son 60 días.", {"30"}) == ["60"]
    assert metrics.unsupported_figures("Tienes 30 días.", {"30", "5"}) == []
    # Echoing a wrong figure while correcting it still counts (strict by design).
    assert metrics.unsupported_figures("No son 90 días, son 30 días", {"30"}) == ["90"]
    assert metrics.unsupported_figures("Son 30 días, no 90.", {"30"}) == []  # no unit


def test_percentile_nearest_rank() -> None:
    values = [float(i) for i in range(1, 101)]
    assert metrics.percentile(values, 95) == 95.0
    assert metrics.percentile(values, 50) == 50.0
    assert metrics.percentile([3.0], 95) == 3.0
    assert metrics.percentile([], 95) == 0.0


def test_mean_and_ratio_of_nothing_are_none() -> None:
    assert metrics.mean([]) is None
    assert metrics.ratio([]) is None
    assert metrics.ratio([True, False, True, True]) == 0.75


def test_check_threshold_directions() -> None:
    assert metrics.check_threshold("recall_at_5", 0.90, 0.90) is True
    assert metrics.check_threshold("recall_at_5", 0.89, 0.90) is False
    assert metrics.check_threshold("latency_p95_s", 3.9, 4.0) is True
    assert metrics.check_threshold("latency_p95_s", 4.0, 4.0) is False
    assert metrics.check_threshold("faithfulness", None, 0.95) is None


def test_thresholds_fail_the_run_when_a_metric_drops() -> None:
    thresholds = report.load_thresholds("full")
    summary: dict[str, float | None] = {
        "recall_at_5": 0.95,
        "faithfulness": 0.90,  # below 0.95
        "citation_precision": 0.95,
        "correct_abstention": 0.95,
        "correct_figures": 1.0,
        "latency_p95_s": 1.0,
        "poisoned_leaks": 0.0,
    }
    failing = report.failed(report.evaluate_thresholds(summary, thresholds))
    assert [r.metric for r in failing] == ["faithfulness"]
    summary["faithfulness"] = 0.96
    summary["poisoned_leaks"] = 1.0
    failing = report.failed(report.evaluate_thresholds(summary, thresholds))
    assert [r.metric for r in failing] == ["poisoned_leaks"]


def test_offline_thresholds_only_gate_what_a_fake_model_can_measure() -> None:
    gated = set(report.load_thresholds("offline"))
    assert gated == {"recall_at_5", "correct_figures", "poisoned_leaks"}
