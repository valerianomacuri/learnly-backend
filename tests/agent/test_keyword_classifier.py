import pytest

from app.agent.nodes.keyword_classifier import classify_by_keywords
from app.agent.state import Intent


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("¿Cuántos días tengo para pedir un reembolso?", Intent.POLICY_QUESTION),
        ("What is the refund policy?", Intent.POLICY_QUESTION),
        ("¿Cómo va mi solicitud?", Intent.REFUND_STATUS),
        ("Quiero un reembolso del curso de Python", Intent.REFUND_REQUEST),
        ("Quiero cancelar mi solicitud", Intent.CANCEL_REFUND),
        ("Quiero hablar con un humano", Intent.HUMAN_HANDOFF),
        ("Dame una receta de paella", Intent.OUT_OF_SCOPE),
    ],
)
def test_keyword_classifier(text: str, expected: Intent) -> None:
    assert classify_by_keywords(text) == expected
