"""Keep team/field names in English: undo translated names in requests.

Voice transcripts and typed requests in Spanish sometimes carry a
translated team or field name (``Zorros`` for ``Foxes``, ``Campo Norte``
for ``North Field``). The model is told names are English proper names,
but a transcript that already translated them leaves it nothing exact to
match, so the team is dropped and the manager is asked again. This
module maps the known Spanish forms of a problem's own team/field names
back to the exact English names before the request reaches the model.
Only names present in the problem are ever produced; anything else is
left untouched (never guessed).
"""

from __future__ import annotations

import re
import unicodedata

from .models import Problem

# English name word -> Spanish forms (singular/plural, unaccented keys).
_ES_FORMS: dict[str, tuple[str, ...]] = {
    "fox": ("zorro", "zorros"),
    "foxes": ("zorro", "zorros"),
    "badger": ("tejon", "tejones"),
    "badgers": ("tejon", "tejones"),
    "otter": ("nutria", "nutrias"),
    "otters": ("nutria", "nutrias"),
    "wolf": ("lobo", "lobos"),
    "wolves": ("lobo", "lobos"),
    "hawk": ("halcon", "halcones"),
    "hawks": ("halcon", "halcones"),
    "eagle": ("aguila", "aguilas"),
    "eagles": ("aguila", "aguilas"),
    "bear": ("oso", "osos"),
    "bears": ("oso", "osos"),
    "lion": ("leon", "leones"),
    "lions": ("leon", "leones"),
    "tiger": ("tigre", "tigres"),
    "tigers": ("tigre", "tigres"),
}

_FIELD_PHRASES: dict[str, tuple[str, ...]] = {
    "north field": ("campo norte", "cancha norte"),
    "south field": ("campo sur", "cancha sur"),
    "east field": ("campo este", "cancha este"),
    "west field": ("campo oeste", "cancha oeste"),
}

_ARTICLES = ("los", "las", "el", "la")


def _fold(text: str) -> str:
    """Lowercase and strip accents for comparison only."""
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _replace_form(text: str, form: str, english: str) -> str:
    pattern = re.compile(
        r"\b(?:(?:" + "|".join(_ARTICLES) + r")\s+)?" + re.escape(form) + r"\b",
        re.IGNORECASE,
    )
    return pattern.sub(english, text)


def normalize_request_names(problem: Problem, request: str) -> str:
    """Return ``request`` with translated team/field names back in English.

    Deterministic and conservative: a Spanish form is replaced only when
    it is the known translation of a team or field name in ``problem``.
    """
    if not isinstance(request, str) or not request.strip():
        return request
    folded = _fold(request)
    out = request
    # Multi-word field phrases first (Campo Norte -> North Field).
    for field in problem.fields:
        forms = _FIELD_PHRASES.get(_fold(field.name))
        if forms:
            for form in forms:
                if form in folded:
                    out = _replace_form(out, form, field.name)
                    folded = _fold(out)
    for team in problem.teams:
        forms: tuple[str, ...] = ()
        for word in _fold(team.name).split():
            forms = _ES_FORMS.get(word, forms)
            if forms:
                break
        for form in forms:
            if re.search(r"\b" + re.escape(form) + r"\b", folded):
                out = _replace_form(out, form, team.name)
                folded = _fold(out)
    return out
