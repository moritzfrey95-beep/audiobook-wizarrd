from abw import titles


def test_series_name_removed_front_back_and_in_brackets():
    assert titles.display("Tarkin: Star Wars", "Star Wars") == "Tarkin"
    assert titles.display("Thrawn (Star Wars)", "Star Wars") == "Thrawn"
    assert titles.display("Star Wars: Queen's Shadow", "Star Wars") == "Queen's Shadow"
    assert titles.display("Star Wars™ - Episode I - Die dunkle Bedrohung", "Star Wars") == "Episode I - Die dunkle Bedrohung"
    assert titles.display("Return of the Jedi: Star Wars: Episode VI", "Star Wars") == "Return of the Jedi: Episode VI"
    assert titles.display("Kenobi: Star Wars Legends", "Star Wars: Legends") == "Kenobi"
    assert titles.display("Star Wars Rebel Rising", "Star Wars") == "Rebel Rising"


def test_sub_series_and_bracket_suffix_of_series():
    assert titles.display("Star Wars: The High Republic: Midnight Horizon", "Star Wars: The High Republic (Young Adult)") == "Midnight Horizon"
    assert titles.display("Star Wars: Tempest Runner (The High Republic)", "Star Wars: The High Republic") == "Tempest Runner"
    assert titles.display("Star Wars: The High Republic:: Mission to Disaster", "Star Wars꞉ The High Republic (Phase II)") == "Mission to Disaster"


def test_full_title_kept_when_nothing_of_its_own_remains():
    for title, series in [("Dao of Healing, Book 1", "Dao of Healing"), ("Die drei ??? und der tanzende Teufel", "Die drei ???"),
                          ("Harry Potter and the Prisoner of Azkaban", "Harry Potter"), ("Baccano!, Vol. 11 (light novel)", "Baccano!"),
                          ("Der Ehrenkodex des Jägers (Buch 8)", "Der Ehrenkodex des Jägers"), ("Wild Cards III: Jokers Wild", "Wild Cards"),
                          ("Death: Genesis 9: An Isekai LitRPG", "Death: Genesis"), ("Dune Messiah", "Dune"),
                          ("Mother of Learning Arc 1", "Mother of Learning"), ("Ready Player One - Enhanced Audio", "Ready Player One"),
                          ("Dickie Dick Dickens, der Revolutionsheld", "Dickie Dick Dickens")]:
        assert titles.display(title, series) == title, title


def _b(i, title, seq=None, owned=0, runtime=None, subtitle=None, asin=None):
    return {"id": i, "title": title, "seq": seq, "owned": owned, "runtime_min": runtime, "subtitle": subtitle, "asin": asin}


def test_collapse_keeps_owned_edition_and_lists_the_other():
    books = [_b(1, "Solo: A Star Wars Story", 27, 1, 600), _b(2, "Solo: A Star Wars Story: Expanded Edition", 27, 0, 700),
             _b(3, "Tarkin: Star Wars", 24, 1, 500)]
    bl, st, also = titles.collapse(books, ["owned", "missing", "owned"], "Star Wars")
    assert [b["id"] for b in bl] == [1, 3] and st == ["owned", "owned"]
    assert [o["id"] for o in also[1]] == [2]


def test_collapse_prefers_owned_even_when_listed_later():
    books = [_b(1, "Star Wars Jedi: Battle Scars", 25, 0, 720), _b(2, "Star Wars Jedi: Battle Scars", 26, 1, 725)]
    bl, st, also = titles.collapse(books, ["missing", "owned"], "Star Wars")
    assert [b["id"] for b in bl] == [2] and st == ["owned"]


def test_same_title_different_volumes_stay_apart():
    # «Dragon Heart»: alle Bände heissen gleich, Nummer und Laufzeit unterscheiden sie
    books = [_b(i, "Dragon Heart", i, 1, 600 + 90 * i) for i in range(1, 5)]
    bl, _, also = titles.collapse(books, ["owned"] * 4, "Dragon Heart Series")
    assert len(bl) == 4 and not also
    # Untertitel widersprechen sich
    books = [_b(1, "Thrawn Ascendancy", 15, 0, None, "Chaos Rising"), _b(2, "Thrawn Ascendancy", None, 1, None, "Greater Good")]
    assert len(titles.collapse(books, ["missing", "owned"], "Star Wars")[0]) == 2
    # Band II in Klammern bleibt eigen
    books = [_b(1, "Star Wars: Thrawn Ascendancy", 15), _b(2, "Star Wars: Thrawn Ascendancy (Book II: Greater Good)", 16)]
    assert len(titles.collapse(books, ["missing", "missing"], "Star Wars")[0]) == 2
