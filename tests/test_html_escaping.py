"""HTML escaping of user-supplied card content before HTML-mode send.

The bot runs with ParseMode.HTML, so card.front / card.back must be
escaped at every interpolation site or attacker-controlled markup will
either render or break message parsing.
"""

from __future__ import annotations

from src.db.models import Card
from src.handlers.review import _format_front, _format_front_back


def _card(front: str, back: str) -> Card:
    return Card(front=front, back=back)


def test_format_front_escapes_html_entities() -> None:
    card = _card(front="<script>x</script> & y", back="A<B>C")

    out = _format_front(card)

    assert "&lt;script&gt;x&lt;/script&gt; &amp; y" in out
    assert "<script>" not in out
    # The literal <b> wrapper tags must remain unescaped.
    assert out.startswith("<b>")
    assert out.endswith("</b>")


def test_format_front_back_escapes_both_fields() -> None:
    card = _card(front="<script>x</script> & y", back="A<B>C")

    out = _format_front_back(card)

    assert "&lt;script&gt;x&lt;/script&gt; &amp; y" in out
    assert "A&lt;B&gt;C" in out
    assert "<script>" not in out
    assert "<B>" not in out
    # The literal <b> wrapper tags must remain unescaped.
    assert out.startswith("<b>")
    assert "</b>" in out
