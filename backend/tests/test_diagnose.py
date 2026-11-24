"""Laufzeitabweichung wird eingeordnet (Sammelpack, andere Aufnahme, unvollständig); Begriffe für Sammelpacks."""
import pytest

from abw import diagnose, scoring


def kind(minutes, want, release="", runtimes=None):
    return diagnose.runtime_finding(minutes, want, release, runtimes)["kind"]


def test_close_runtimes_stay_ok_or_warn():
    assert kind(111, 111) == "ok" and kind(150, 111) == "close"


def test_narnia_pack_is_another_recording_not_just_too_long():
    f = diagnose.runtime_finding(2040, 111, "Clive Staples Lewis Die Chroniken von Narnia - Folge 01-07 (ungekrzt) (Sammelpack)", [109, 111, 111, 110, 110, 109])
    assert f["status"] == "fail" and f["kind"] == "pack_other" and f["volumes"] == 7
    assert "Sammelpack" in f["text"] and "andere Aufnahme" in f["text"] and "7 Bände" in f["text"]


def test_pack_with_matching_runtime_per_volume_is_splittable():
    assert kind(111 * 4, 111, "Serie 1-4") == "pack"
    assert kind(3 * 120, 120, "", [120, 120, 120]) == "pack"  # entspricht der ganzen Serie


def test_long_download_without_evidence_is_not_called_a_pack():
    """«Wheel of Time 11» mit 33 h gegen 1 h 34 bei Audible: kein Sammelwort, kein Bereich, Serie passt nicht: nichts behaupten."""
    f = diagnose.runtime_finding(1981, 94, "The.Gathering.Storm.Wheel.of.Time.Book.11", [94, 700, 800])
    assert f["kind"] == "long_unnamed" and "Sammelpack" not in f["text"]


def test_short_and_other():
    assert kind(100, 240) == "short" and kind(320, 240) == "other"


@pytest.mark.parametrize("title,span", [
    ("Zogarth-Primal Hunter 01 - 07-AUDiOBOOK-7CD-MP3-DE-2026-PaZ", (1, 7)),
    ("Newman.Emma-Planetfall.1-4.2015-2019.Emma.Newman", (1, 4)),
    ("Alisa.Woods.-.Fallen.Immortals.Series.1-9", (1, 9)),
    ("Dragon Emperor 1-22 - Complete - Batch - Eric Vall [MP3]", (1, 22)),
    ("Die Chroniken von Narnia - Folge 01-07 (ungekrzt) (Sammelpack)", (1, 7)),
    ("Resident Evil 2 - 3 Wishes", None), ("Book 2 - 1-800-Starship", None), ("Reihe 128 Kbps 2 - 4 kbps", None),
])
def test_bare_ranges(title, span):
    assert scoring.span(title) == span


@pytest.mark.parametrize("title", ["The Thrawn Trilogy", "Der Herr der Ringe Trilogie", "All Six Books, Fully Chaptered", "Narnia Sammelpack",
                                   "Harry Potter 3-in-1", "Dragon Emperor Batch", "Die komplette Reihe", "Whole Series"])
def test_pack_words(title):
    assert scoring.is_multi(scoring.plain(title)), title


@pytest.mark.parametrize("title", ["The Sound of Change (The Bards and Dragons Saga 2)", "Das Große Spiel 01", "Wheel of Time 11 The Gathering Storm"])
def test_single_volumes_are_not_packs(title):
    assert not scoring.is_multi(scoring.plain(title)), title
