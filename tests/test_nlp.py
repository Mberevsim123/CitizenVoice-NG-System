"""Unit tests for the NLP layer and the serving fallback."""
import pytest

from app.nlp import clean, lexicon_hits, triage, Models, CRITICAL_TERMS


@pytest.fixture(scope="module")
def served():
    return Models()


@pytest.fixture(scope="module")
def classical_only():
    return Models(use_transformer=False)


def test_pidgin_normalisation():
    assert clean("Abeg, NEPA don take light o!!") == "please electricity has take light !!"


def test_lexicon_hits():
    assert lexicon_hits("Gunmen are shooting", CRITICAL_TERMS) == ["gun", "gunmen", "shooting"]


@pytest.mark.parametrize("text,category,urgency", [
    ("A fire has broken out at the market in Lafia and it is spreading fast. Hurry!", "Fire", "High"),
    ("Flood has submerged our houses in Doma and families are stranded on the roof.", "Flood/Emergency", "High"),
    ("I want to know when the scholarship portal will open. Thank you.", "Education", "Low"),
])
def test_triage_examples(served, text, category, urgency):
    a = triage(text, served)
    assert a["category"] == category
    assert a["urgency"] == urgency
    assert 0 <= a["score"] <= 100


def test_probabilities_are_distributions(served):
    p = served.predict(clean("the road to the general hospital is impassable"))
    for task, probs in p.items():
        assert abs(sum(probs.values()) - 1) < 1e-6, task


def test_classical_fallback_works(classical_only):
    assert classical_only.transformer is None
    assert classical_only.backend == "classical"
    a = triage("Kidnappers just abducted two people from our street in Keffi right now!", classical_only)
    assert a["urgency"] == "High" and a["category"] == "Security"


def test_cluster_raises_score(served):
    text = "Every time it rains the drainage overflows into our houses in Lafia."
    assert triage(text, served, cluster_count=5)["score"] > triage(text, served)["score"]
