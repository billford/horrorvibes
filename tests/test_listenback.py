# pylint: disable=missing-function-docstring
import pytest

from horrorvibes import listenback


@pytest.mark.parametrize(
    "name,respelling,transcript",
    [
        ("Ngaire", "NYE-ree", "and Nyree Crockett said"),
        ("Kaikōura", "kye-KOH-rah", "over the Kaikoura coast"),
        ("Chir-Batti", "cheer BAH-tee", "the cheer batty lights"),
        ("Baychimo", "BAY-chih-moh", "the SS Bay Chimo drifted"),
        ("Tutankhamun", "too-tank-AH-mun", "Tutankhamen's trumpets"),
    ],
)
def test_sound_alike_spellings_pass(name, respelling, transcript):
    assert listenback.name_heard(name, respelling, transcript)


@pytest.mark.parametrize(
    "name,respelling,transcript",
    [
        ("Ogopogo", "OH-go-PO-go", "the Okanagan monster"),
        ("Tappern", "TAP-ern", "James Topham played"),
        ("Baychimo", "BAY-chih-moh", "the ship drifted on"),
    ],
)
def test_garbled_or_skipped_names_fail(name, respelling, transcript):
    assert not listenback.name_heard(name, respelling, transcript)


def test_missing_names_only_checks_names_in_this_text():
    names = [
        {"name": "Ogopogo", "respelling": "OH-go-PO-go"},
        {"name": "Kelowna", "respelling": "keh-LOH-nuh"},
    ]
    missing = listenback.missing_names("they saw the Okanagan thing", "They saw Ogopogo.", names)
    assert [m["name"] for m in missing] == ["Ogopogo"]


def test_respell_swaps_names_case_insensitively():
    entries = [{"name": "Ngaire", "respelling": "NYE-ree"}]
    assert listenback.respell("ngaire saw it. Ngaire ran.", entries) == "NYE-ree saw it. NYE-ree ran."


def test_fold_strips_accents_and_punctuation():
    assert listenback.fold("Kaikōura's Lights!") == "kaikoura s lights"
