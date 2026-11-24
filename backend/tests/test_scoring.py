"""Bewertung an echten Release-Namen. Start: cd backend && python -m pytest tests"""
from abw.scoring import score

SIB = ["Dungeon Crawler Carl", "Carl's Doomsday Scenario", "The Dungeon Anarchist's Cookbook", "A Parade of Horribles"]
EN1 = dict(title="Dungeon Crawler Carl", author="Matt Dinniman", series="Dungeon Crawler Carl", sequence="1", lang="en", runtime_min=810)
EN8 = dict(title="A Parade of Horribles", author="Matt Dinniman", series="Dungeon Crawler Carl", sequence="8", lang="en", runtime_min=1700)
DE2 = dict(title="Carl’s Doomsday Scenario", author="Matt Dinniman", series="Dungeon Crawler Carl (German Edition)", sequence="2", lang="de", runtime_min=800)


def verdict(book, title, size=1_300_000_000):
    return score(book, {"title": title, "size": size}, SIB)["verdict"]


def test_right_volume_is_safe():
    assert verdict(EN8, "Matt.Dinniman.-.Dungeon.Crawler.Carl,.Book.8.-.A.Parade.of.Horribles") == "sicher"
    assert verdict(EN1, "Matt Dinniman - Dungeon Crawler Carl (Book 1) m4b", 700_000_000) == "sicher"
    assert verdict(DE2, "Matt.Dinniman-Dungeon.Crawler.Carl.02-Carl’s.Doomsday.Scenario.(Ungekürzt)") == "sicher"


def test_other_volume_is_rejected():
    assert verdict(EN1, "Matt.Dinniman.-.Dungeon.Crawler.Carl,.Book.8.-.A.Parade.of.Horribles") == "verworfen"
    assert verdict(EN8, "Matt Dinniman - Dungeon Crawler Carl (Book 1) m4b") == "verworfen"
    assert verdict(EN1, "Matt.Dinniman-Dungeon.Crawler.Carl.02-Carl’s.Doomsday.Scenario.(Ungekürzt)") == "verworfen"


def test_wrong_language_and_type():
    assert verdict(EN1, "Matt Dinniman - Dungeon Crawler Carl Svensk Utgaava-AUDiOBOOK-WEB-SE-2") == "verworfen"
    assert verdict(EN1, "Matt Dinniman - Dungeon Crawler Carl 01 epub", 2_000_000) == "verworfen"
    # Sammelband mit dem gesuchten Band: zählt wie ein Einzelband und wird nach dem Download aufgeteilt (hier ohne Autor nur unsicher)
    assert verdict(EN1, "Dungeon Crawler Carl Books 1-5", 4_000_000_000) == "unsicher"
    assert verdict(EN8, "Dungeon Crawler Carl Books 1-5", 4_000_000_000) == "verworfen"
    assert verdict(EN1, "Dungeon.Crawler.Carl.-.Season.2.-.Audio.Immersion.Tunnel.[Untouched]") != "sicher"


def test_too_small_is_never_safe():
    assert verdict(EN8, "Matt.Dinniman.-.Dungeon.Crawler.Carl,.Book.8.-.A.Parade.of.Horribles", 90_000_000) != "sicher"


def test_series_prefix_alone_is_not_a_title_match():
    book = dict(title="Star Wars: Convergence", author="Zoraida Córdova", series="Star Wars: The High Republic", sequence="39", lang="en", runtime_min=700)
    r = score(book, {"title": "Rogue Squadron: Star Wars Legends: The X-Wing Series, Book 1 by Michael A. Stackpole", "size": 400_000_000}, [])
    assert r["verdict"] != "sicher"
    assert score(book, {"title": "Zoraida Cordova - Star Wars The High Republic - Convergence", "size": 400_000_000}, [])["verdict"] == "sicher"


def test_dead_torrent_is_never_safe():
    rel = {"title": "Matt Dinniman - Dungeon Crawler Carl (Book 1) m4b", "size": 700_000_000}
    assert score(EN1, {**rel, "seeders": 0}, SIB)["verdict"] != "sicher"
    assert score(EN1, {**rel, "seeders": 12}, SIB)["verdict"] == "sicher"


def test_german_book_with_english_title_needs_language_marker():
    de1 = dict(title="Dungeon Crawler Carl", author="Matt Dinniman", series="Dungeon Crawler Carl (German Edition)", sequence="1",
               lang="de", runtime_min=800, same_title_other_lang=True)
    assert score(de1, {"title": "Dungeon Crawler Carl - Matt Dinniman [M4B]", "size": 800_000_000, "seeders": 5}, SIB)["verdict"] != "sicher"
    assert score(de1, {"title": "Matt Dinniman - Dungeon Crawler Carl 01 (Ungekürzt) German", "size": 800_000_000}, SIB)["verdict"] == "sicher"


def test_spanish_de_is_not_a_german_marker():
    de1 = dict(title="Dungeon Crawler Carl", author="Matt Dinniman", series="Dungeon Crawler Carl", sequence="1", lang="de", runtime_min=800)
    texts = [x["text"] for x in score(de1, {"title": "El Portal de los dioses salvajes: Dungeon Crawler Carl - Matt Dinniman", "size": 800_000_000}, SIB)["reasons"]]
    assert "Als deutsch gekennzeichnet" not in texts
    texts = [x["text"] for x in score(de1, {"title": "Matt_Dinniman-Dungeon_Crawler_Carl-AUDiOBOOK-DE-2026", "size": 800_000_000}, SIB)["reasons"]]
    assert "Als deutsch gekennzeichnet" in texts


def test_bare_number_of_other_volume_is_rejected_when_volumes_share_a_title():
    # Band 2 gesucht, "… LitRPG 1 - Cattacomb" galt als sicher
    sib = ["Demonic City Builder: A Base & Kingdom Building LitRPG (Book 1)", "Demonic City Builder: A Base & Kingdom Building LitRPG (Book 3)"]
    b2 = dict(title="Demonic City Builder: A Base & Kingdom Building LitRPG (Book 2)", author="Cattacomb", series="Demonic City Builder",
              sequence="2", lang="en", runtime_min=924)
    rel = lambda t: score(b2, {"title": t, "size": 836_000_000, "seeders": 1}, sib)["verdict"]
    assert rel("Demonic City Builder: A Base & Kingdom Building LitRPG 1 - Cattacomb [M4B] [128 Kbps]") == "verworfen"
    assert rel("Demonic City Builder: A Base & Kingdom Building LitRPG - Cattacomb [M4B] [128 Kbps]") != "sicher"
    assert rel("Demonic City Builder: A Base & Kingdom Building LitRPG 2 - Cattacomb [M4B] [128 Kbps]") == "sicher"
    assert rel("Demonic City Builder, Book 2 - Cattacomb [M4B]") == "sicher"


def test_single_word_before_colon_is_not_a_title_match():
    """«Apocalypse: Generic System» darf nicht auf jedes Buch mit «Apocalypse» und Band 1 passen."""
    book = dict(title="Apocalypse: Generic System", author="Macronomicon", series="The Stitched Worlds", sequence="1", lang="en", runtime_min=646)
    sib = ["Apocalypse: Fairy System", "Apocalypse: Quest System", "Apocalypse: Dungeon System"]
    v = lambda t, **kw: score(book, {"title": t, "size": 700_000_000, **kw}, sib)["verdict"]
    assert v("Goblin Apocalypse, Book 1 - Michael Dalton [M4B] [128 Kbps]", source="audiobookbay", seeders=1) == "verworfen"
    assert v("Apocalypse.Always.Apocalypse.Always,.Book.1") == "verworfen"
    assert v("Apocalypse: Fairy System - Macronomicon [M4B] [128 Kbps]", source="audiobookbay", seeders=1) == "verworfen"
    assert v("Apocalypse: Generic System Systems of the Apocalypse, Book 1 - Macronomicon", source="audiobookbay", seeders=1) == "sicher"
    sw = dict(title="Star Wars: Convergence", author="Zoraida Córdova", series="Star Wars", sequence="1", lang="en", runtime_min=808)
    assert score(sw, {"title": "Star Wars-Thrawn 01", "size": 2_400_000_000}, ["Star Wars: Thrawn"])["verdict"] != "sicher"
    tiny = dict(title="Tiny Dungeon Core 2: A LitRPG Adventure", author="X Y", series="Tiny Dungeon Core", sequence="2", lang="en", runtime_min=500)
    assert score(tiny, {"title": "Tiny.Dungeon.Core.Book.2", "size": 400_000_000}, ["Tiny Dungeon Core: A LitRPG Adventure"])["verdict"] == "sicher"
    sea = dict(title="A Wizard of Earthsea", author="Ursula K. Le Guin", series="Earthsea", sequence="1", lang="en", runtime_min=430)
    assert score(sea, {"title": "A Wizard of Earthsea - BBC Radio - Ursula K. Le Guin [MP3]", "size": 200_000_000, "source": "audiobookbay"}, [])["verdict"] != "sicher"
    assert score(sea, {"title": "A Wizard of Earthsea - Ursula K. Le Guin [MP3]", "size": 200_000_000, "source": "audiobookbay", "seeders": 1}, [])["verdict"] == "sicher"


DD = [f"Dungeon Diving {n}" for n in (101, 102, 103, 104, 201, 202, 203, 204, 301, 302, 303)]


def dd(seq):
    return dict(title=DD[seq - 1], author="Bruce Sentar", series="Dungeon Diving", sequence=str(seq), lang="en", runtime_min=600)


def test_number_in_title_is_not_a_volume_number():
    # «Dungeon Diving 102» ist Band 2: die Zahl gehört zum Titel und unterscheidet die Bände
    r = score(dd(2), {"title": "Dungeon Diving 102 - Bruce Sentar [M4B] [128 Kbps]", "size": 600_000_000}, DD)
    assert r["verdict"] == "sicher", r["reasons"]
    assert not any("Bandnummer" in x["text"] or "heissen gleich" in x["text"] for x in r["reasons"])
    assert score(dd(9), {"title": "Dungeon Diving 301 - Bruce Sentar [M4B] [128 Kbps]", "size": 600_000_000}, DD)["verdict"] == "sicher"
    # Nummer eines anderen Bandes verrät ihn
    r = score(dd(1), {"title": "Bruce.Sentar-Dungeon.Diving.303", "size": 600_000_000}, DD)
    assert r["verdict"] == "verworfen" and any(x["text"] == "Titel eines anderen Bandes: Dungeon Diving 303" for x in r["reasons"])
    # ausdrückliche Bandnummer gilt weiterhin
    assert score(dd(3), {"title": "Dungeon Diving 103 - Book 3 - Bruce Sentar [M4B]", "size": 600_000_000}, DD)["verdict"] == "sicher"


def test_single_volume_of_a_complete_series_is_no_bundle():
    hp = dict(title="Harry Potter and the Chamber of Secrets", author="J.K. Rowling", series="Harry Potter", sequence="2", lang="en", runtime_min=570)
    r = score(hp, {"title": "Harry Potter (by J.K. Rowling) - The Complete Story (read by Stephen Fry) [V0] Book 2 - Harry Potter and the Chamber of Secrets",
                   "size": 845_211_425}, ["Harry Potter and the Philosopher's Stone", "Harry Potter and the Chamber of Secrets"])
    assert r["verdict"] == "sicher" and not any("Sammelband" in x["text"] for x in r["reasons"])


def test_dash_after_book_number_is_no_range():
    ns = dict(title="1-800-Starship, Book 2: The Last Emperor", author="J.N. Chaney", series="1-800-Starship", sequence="2", lang="en", runtime_min=600)
    r = score(ns, {"title": "J.N. Chaney - 1-800-Starship, Book 2 - 1-800-Starship, Book 2: The Last Emperor", "size": 600_000_000}, [])
    assert not any("Sammelband" in x["text"] for x in r["reasons"]), r["reasons"]
    es = dict(title="86--EIGHTY-SIX, Vol. 9", author="Asato Asato", series="86--EIGHTY-SIX", sequence="9", lang="en", runtime_min=500)
    r = score(es, {"title": "Asato Asato, Shirabii, Roman Lempert – translator - 86–EIGHTY-SIX, Book 9 - 86–EIGHTY-SIX, Vol. 9", "size": 500_000_000}, [])
    assert not any("Sammelband" in x["text"] for x in r["reasons"]), r["reasons"]


def test_bundle_ranges_with_hash_and_bracketed_series():
    sb = dict(title="Slow Burn 3", author="Bobby Adair", series="Slow Burn", sequence="3", lang="en", runtime_min=400)
    r = score(sb, {"title": "Slow Burn Box Set (Slow Burn #1-9) - Bobby Adair [M4B] [64 Kbps]", "size": 3_000_000_000}, [])
    assert r["bundle"] == [1, 9]
    wa = dict(title="Die Welt aufleveln 2", author="L. Eclaire", series="Die Welt Aufleveln [Level Up the World]", sequence="2", lang="de", runtime_min=700)
    r = score(wa, {"title": "Die Welt aufleveln 1-3 Epic LitRPG Boxset - L. Eclaire [M4B] [64 Kbps]", "size": 2_000_000_000}, [])
    assert r["bundle"] == [1, 3]


def test_small_numbers_do_not_make_titles_distinct():
    narr = [f"Das Zeichen des Narren {n}" for n in range(1, 7)]
    b = dict(title=narr[2], author="J.M. Clarke", series="Das Zeichen des Narren", sequence="3", lang="de", runtime_min=900)
    r = score(b, {"title": "J.M.Clarke.-.Das.Zeichen.des.Narren.(Ungekürzt),.ABOOK,.mp3", "size": 900_000_000}, narr)
    assert r["verdict"] != "sicher"
    sv = dict(title="The Anime Trope System: Stone vs. Viper, Book 16", author="Alvin Atwater", series="The Anime Trope System", sequence="16", lang="en", runtime_min=500)
    assert any(x["text"] == "Bandnummer 16 passt" for x in score(sv, {"title": "The Anime Trope System: Stone vs. Viper Bk 16 - Alvin Atwater [MP3] [64 Kbps]", "size": 400_000_000}, [])["reasons"])


def test_other_series_with_same_first_word_and_number_is_not_safe():
    ha = dict(title="Dune: House Atreides: House Trilogy, Book 1", author="Brian Herbert, Kevin J. Anderson", series="DUNE: Prelude to Dune", sequence="1", lang="en", runtime_min=1300)
    sib = ["House Harkonnen: House Trilogy, Book 2", "House Corrino: House Trilogy, Book 3"]
    assert score(ha, {"title": "Brian Herbert - Legends of Dune, Bk 1 - The Butlerian Jihad (NMR", "size": 900_000_000}, sib)["verdict"] != "sicher"
    assert score(ha, {"title": "Brian Herbert - Prelude To Dune, Bk 1 - House Atreides (NMR", "size": 900_000_000}, sib)["verdict"] == "sicher"


def test_title_numbers_helper():
    from abw.scoring import title_numbers
    assert title_numbers("Dungeon Diving 101", "Dungeon Diving") == {"101"}
    assert title_numbers("Das Zeichen des Narren 3", "Das Zeichen des Narren") == set()
    assert title_numbers("Fahrenheit 1984 (2019)", "") == set()  # Jahreszahl
    assert title_numbers("Dungeon Diving 101 - Bruce Sentar [M4B] [128 Kbps]") == {"101"}


def test_complete_series_without_numbers_is_unsure():
    ac = dict(title="Accidental Necromancer", author="JG Jerome", series="Accidental Necromancer", sequence="1", lang="en", runtime_min=600)
    r = score(ac, {"title": "Accidental Necromancer Boxed Set (complete series omnibus) - JG Jerome [M4B] [64 Kbps]", "size": 1_500_000_000}, ["Accidental Necromancer 2"])
    assert r["verdict"] == "unsicher" and r["bundle"] is None, r["reasons"]
    # fremde Sammlung ohne Serienname bleibt verworfen
    em = dict(title="Emerilia 9", author="Michael Atamanov", series="Emerilia", sequence="9", lang="en", runtime_min=600)
    assert score(em, {"title": "Egyptian Mythology: A Timeless Collection of Egyptian Myths and Legends - Adrian Danvers [MP3]", "size": 300_000_000}, [])["verdict"] == "verworfen"
    # «(12 books)» ist Band 1–12
    rb = dict(title="Reality Benders 12", author="Michael Atamanov", series="Reality Benders", sequence="12", lang="en", runtime_min=600)
    r = score(rb, {"title": "Reality Benders - The complete Set (12 books) - Michael Atamanov, Andrew Schmitt - translator [Mixed]", "size": 6_000_000_000}, [])
    assert r["bundle"] == [1, 12]


def test_folge_range_is_a_bundle():
    br = dict(title="Die Gefahren einer Hauptstadt", author="Tao Wong", series="Abenteuer in Brad", sequence="8", lang="de", runtime_min=286)
    r = score(br, {"title": "Tao Wong - Abenteuer in Brad - Folge 01 - 08 (Ungekrzt)", "size": 2_300_000_000}, [])
    assert r["bundle"] == [1, 8] and not any("Andere Bandnummer" in x["text"] for x in r["reasons"]), r["reasons"]
    assert r["verdict"] == "sicher", r["reasons"]
    # andere Serie mit Bereich bleibt draussen
    assert score(br, {"title": "Tao Wong - A Thousand Li Series Books 1-10", "size": 2_300_000_000}, [])["verdict"] == "verworfen"


def test_bestimmte_fassung_im_seriennamen():
    """«Harry Potter (Narrated by Stephen Fry)» ist nicht die Jim-Dale-Fassung, «BBC Radio's …» keine Lesung."""
    from abw import usenet
    book = {"title": "Harry Potter and the Chamber of Secrets (Narrated by Stephen Fry)", "author": "J.K. Rowling",
            "series": "Harry Potter (Narrated by Stephen Fry)", "sequence": "2", "lang": "en", "runtime_min": 600}
    fry = score(book, {"title": "J.K. Rowling - Harry Potter 02 - Harry Potter and the Chamber of Secrets (Stephen Fry) [M4B]", "size": 600e6})
    dale = score(book, {"title": "J.K. Rowling - Harry Potter 02 - Harry Potter and the Chamber of Secrets [M4B]", "size": 600e6})
    assert fry["verdict"] == "sicher" and dale["verdict"] == "unsicher"
    assert usenet.queries(book)[0] == "Harry Potter and the Chamber of Secrets Stephen Fry"
    assert "Stephen Fry" not in " ".join(usenet.queries(book, audio=False))
    bbc = {"title": "The Lord of the Rings: The Two Towers", "author": "J.R.R. Tolkien", "series": "BBC Radio’s The Lord of the Rings",
           "sequence": "2", "lang": "en", "runtime_min": 240, "drama_series": 1}
    assert usenet.queries(bbc)[0] == "The Lord of the Rings BBC"
    assert score(bbc, {"title": "J.R.R. Tolkien - The Lord of the Rings - The Two Towers (BBC Radio Dramatisation) Book 2", "size": 200e6})["verdict"] == "sicher"
    assert score(bbc, {"title": "J.R.R. Tolkien - The Lord of the Rings - The Two Towers Book 2 Unabridged", "size": 200e6})["verdict"] == "unsicher"
    plain_book = {**book, "title": "Harry Potter and the Chamber of Secrets", "series": "Harry Potter"}
    assert score(plain_book, {"title": "J.K. Rowling - Harry Potter 02 - Harry Potter and the Chamber of Secrets [M4B]", "size": 600e6})["verdict"] == "sicher"


def test_band_null_komma_fuenf():
    """Fall 17.11.: «Book 0.5» galt als Band «.5» und damit als andere Bandnummer."""
    from abw.scoring import numbers
    assert numbers("Book 0.5 - The Fall") == {"0.5"}
    assert numbers("Book 03") == {"3"} and numbers("Book 2.0") == {"2"}
    fall = dict(title="The Fall", author="Ryan Cahill", series="The Bound and the Broken", sequence="0.5", lang="en", runtime_min=240)
    r = score(fall, {"title": "Ryan Cahill - The Bound and The Broken, Book 0.5 - The Fall: The Bound and The Broken Novella", "size": 142_000_000}, [])
    assert not any("Andere Bandnummer" in x["text"] for x in r["reasons"]), r["reasons"]
    assert r["verdict"] != "verworfen", r["reasons"]


def test_englischer_release_ist_fuer_deutsche_ausgabe_nie_sicher():
    """17.11.: «Syl: Diffusion, Book 3 - Lunadea [M4B]» war für die deutsche Ausgabe sicher, weil die Serien nicht verknüpft sind."""
    de = dict(title="Diffusion: Ein Monster-Evolutions-LitRPG-Abenteuer!", author="Lunadea", series="Syl", sequence="3", lang="de", runtime_min=700)
    assert score(de, {"title": "Syl: Diffusion, Book 3 - Lunadea [M4B] [128 Kbps]", "size": 600_000_000}, [])["verdict"] != "sicher"
    assert score(de, {"title": "Lunadea.-.Syl.03.-.Diffusion.(Ungekürzt),.ABOOK,.mp3", "size": 600_000_000}, [])["verdict"] == "sicher"


HAINISH = {"1": "Rocannon's World", "2": "Planet of Exile", "3": "City of Illusions", "4": "The Left Hand of Darkness"}


def test_bundle_naming_only_titles():
    """Sammelband nennt seine Bände nur mit Titeln (offen seit 14.11.): Bereich aus den Titeln, nach dem Download aufgeteilt."""
    rts = {"1": 300, "2": 290, "3": 420, "4": 840}
    sib = [t for k, t in HAINISH.items() if k != "2"]
    b = dict(title="Planet of Exile", author="Ursula K. Le Guin", series="Hainish Cycle", sequence="2", lang="en", runtime_min=290,
             seq_runtimes=rts, seq_titles=HAINISH)
    rel = "Ursula K. Le Guin - Worlds of Exile and Illusion: Rocannon's World; Planet of Exile; City of Illusions [M4B]"
    r = score(b, {"title": rel, "size": 900_000_000}, sib)
    assert r["bundle"] == [1, 3] and r["verdict"] == "sicher", r["reasons"]
    assert any("nennt die Titel der Bände 1–3" in x["text"] for x in r["reasons"])
    # Band 4 ist nicht dabei: andere Bände genannt, nicht sicher
    b4 = {**b, "title": "The Left Hand of Darkness", "sequence": "4", "runtime_min": 840}
    r = score(b4, {"title": rel, "size": 900_000_000}, [t for k, t in HAINISH.items() if k != "4"])
    assert r["verdict"] == "verworfen" and r["bundle"] is None, r["reasons"]
    # nur ein Titel genannt: kein Sammelband
    r = score(b, {"title": "Ursula K. Le Guin - Planet of Exile [M4B]", "size": 300_000_000}, sib)
    assert r["bundle"] is None and r["verdict"] == "sicher", r["reasons"]
    # Lücke (Band 1 und 3, nicht 2): kein Bereich
    b1 = {**b, "title": "Rocannon's World", "sequence": "1", "runtime_min": 300}
    r = score(b1, {"title": "Le Guin - Rocannon's World & City of Illusions", "size": 700_000_000}, [t for k, t in HAINISH.items() if k != "1"])
    assert r["bundle"] is None, r["reasons"]


def test_series_word_titles_do_not_make_a_bundle():
    """«Dune» steckt in jedem Dune-Band: ein Titel aus Serienwörtern zählt nicht für den Bereich."""
    titles = {"1": "Dune", "2": "Dune Messiah", "3": "Children of Dune"}
    b = dict(title="Dune Messiah", author="Frank Herbert", series="Dune", sequence="2", lang="en", runtime_min=530,
             seq_runtimes={"1": 1300, "2": 530, "3": 950}, seq_titles=titles)
    r = score(b, {"title": "Frank Herbert - Dune Messiah [M4B]", "size": 500_000_000}, ["Dune", "Children of Dune"])
    assert r["bundle"] is None and r["verdict"] == "sicher", r["reasons"]


def test_first_title_used_as_series_label_is_no_bundle():
    """«Weirdest Noob 02 - A Slave in the Locked Lands»: Band-1-Titel als Serienname mit Nummer, kein Sammelband 1–2."""
    titles = {"1": "The Weirdest Noob", "2": "A Slave in the Locked Lands", "3": "The Gods of the Second World"}
    b = dict(title="The Weirdest Noob", author="Arthur Stone", series="The Weirdest Noob", sequence="1", lang="en", runtime_min=600,
             seq_runtimes={"1": 600, "2": 650, "3": 700}, seq_titles=titles)
    r = score(b, {"title": "Arthur Stone - Weirdest Noob 02 - A Slave in the Locked Lands (Unabr - M4B)", "size": 600_000_000},
              [t for k, t in titles.items() if k != "1"])
    assert r["bundle"] is None and r["verdict"] != "sicher", r["reasons"]
