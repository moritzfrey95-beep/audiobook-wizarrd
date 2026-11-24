"""Fassungen innerhalb einer Sprache (18.11.2026): Sprecher aufteilen, eigene Karten zusammenlegen, Suche und Prüfung."""
import itertools

from abw import audible, db, importer, variants
from abw.scoring import score

_n = itertools.count()


def series(c, name, lang="en"):
    key = db.norm(name)
    sid = c.execute("INSERT INTO series(name, norm) VALUES(?,?)", (name, key)).lastrowid
    eid = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)", (sid, lang, name, key)).lastrowid
    return sid, eid


def book(c, eid, seq, title, narrator, owned=1, runtime=600, lang="en", path=None):
    i = next(_n)
    return c.execute("INSERT INTO book(edition_id, lang, asin, title, narrator, sequence, seq, owned, abs_item_id, runtime_min, rel_path)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                     (eid, lang, f"A{i:09d}", title, narrator, str(seq), float(seq), owned, f"it-{i}" if owned else None, runtime, path)).lastrowid


def lotr(c, name):
    sid, eid = series(c, name)
    for s, t in ((1, "Fellowship"), (2, "Two Towers"), (3, "Return")):
        book(c, eid, s, t, "Andy Serkis", runtime=1300)
        book(c, eid, s, t, "Rob Inglis", runtime=1100)
    book(c, eid, 0, "Silmarillion", "Martin Shaw")
    return sid, eid


def test_sprecher_werden_aufgeteilt():
    db.init()
    c = db.conn()
    sid, eid = lotr(c, "Ringe Aufteilen")
    variants.assign(c)
    base = c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone()
    v = c.execute("SELECT * FROM edition WHERE base_id=?", (eid,)).fetchall()
    assert base["narr"] == "Andy Serkis" and base["variant"] is None
    assert len(v) == 1 and v[0]["variant"] == "Rob Inglis" and v[0]["series_id"] == sid
    assert {b["narrator"] for b in c.execute("SELECT narrator FROM book WHERE edition_id=?", (v[0]["id"],))} == {"Rob Inglis"}
    # Einzelner dritter Sprecher bleibt in der Hauptfassung
    assert c.execute("SELECT COUNT(*) FROM book WHERE edition_id=?", (eid,)).fetchone()[0] == 4
    # Neues Buch aus derselben Audiobookshelf-Serie landet nach Sprecher in der Fassung
    assert variants.route(c, eid, "Rob Inglis") == v[0]["id"]
    assert variants.route(c, eid, "Andy Serkis") == eid
    # zweiter Lauf ändert nichts
    variants.assign(c)
    assert len(c.execute("SELECT * FROM edition WHERE base_id=?", (eid,)).fetchall()) == 1


def test_kein_aufteilen_ohne_doppelte_baende():
    db.init()
    c = db.conn()
    _, eid = series(c, "Wechselnde Sprecher")
    book(c, eid, 1, "Eins", "Bea A")
    book(c, eid, 2, "Zwei", "Bea A")
    book(c, eid, 3, "Drei", "Bert B")
    book(c, eid, 4, "Vier", "Bert B")  # Sprecherwechsel mitten in der Serie ist keine zweite Fassung
    assert variants.split_plan(c, c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone()) is None


def test_eigene_karten_werden_fassung_der_hauptserie():
    db.init()
    c = db.conn()
    sid, eid = series(c, "Mittelerde Saga")
    book(c, eid, 1, "Eins", "Andy Serkis")
    bsid, beid = series(c, "BBC Radio’s Mittelerde Saga")
    book(c, beid, 1, "Eins", "Ian Holm, Bill Nighy")
    dsid, deid = series(c, "Mittelerde Saga - Phil Dragash")
    book(c, deid, 1, "Eins", "Phil Dragash")
    book(c, deid, 2, "Zwei", "Phil Dragash")
    fsid, feid = series(c, "Ken Follett - Die Nadel")
    book(c, feid, 1, "Die Nadel", "Frank Arnold")
    c.execute("INSERT INTO follow(user_id, kind, key, created) VALUES(1, 'series', ?, 'x')", (str(bsid),))
    variants.assign(c)
    bbc = c.execute("SELECT * FROM edition WHERE id=?", (beid,)).fetchone()
    drag = c.execute("SELECT * FROM edition WHERE id=?", (deid,)).fetchone()
    assert bbc["series_id"] == sid and bbc["variant"] == "BBC-Hörspiel" and bbc["drama"] == 1
    assert drag["series_id"] == sid and drag["variant"] == "Phil Dragash" and drag["narr"] == "Phil Dragash"
    assert not c.execute("SELECT 1 FROM series WHERE id IN (?,?)", (bsid, dsid)).fetchone()
    assert c.execute("SELECT 1 FROM follow WHERE kind='series' AND key=?", (str(sid),)).fetchone()
    assert c.execute("SELECT series_id, variant FROM edition WHERE id=?", (feid,)).fetchone()["series_id"] == fsid
    # Von Hand getrennt: bleibt getrennt
    variants.detach(c, beid)
    c.execute("UPDATE edition SET series_id=? WHERE id=?", (c.execute("INSERT INTO series(name, norm) VALUES('x','x')").lastrowid, beid))
    variants.assign(c)
    assert c.execute("SELECT series_id FROM edition WHERE id=?", (beid,)).fetchone()["series_id"] != sid


def test_gesprochen_von_und_hoerspiel_im_namen():
    db.init()
    c = db.conn()
    mk = lambda name: variants.card_marker(c, {"id": -1, "name": name})
    assert mk("Harry Potter - Gesprochen von Rufus Beck") == {"base": "Harry Potter", "variant": "Rufus Beck", "narr": "Rufus Beck"}
    assert mk("Die Chroniken von Narnia [Hörspiel]")["variant"] == "Hörspiel"
    assert mk("Star Wars: X-Wing - Legends (abridged)") == {"base": "Star Wars: X-Wing - Legends", "variant": "Gekürzt", "narr": None}
    assert mk("Harry Potter (Narrated by Stephen Fry)")["narr"] == "Stephen Fry"
    assert mk("System Apocalypse - Relentless") is None


def test_audible_abgleich_nimmt_nur_den_sprecher_der_fassung():
    db.init()
    c = db.conn()
    _, eid = lotr(c, "Ringe Audible")
    variants.assign(c)
    v = c.execute("SELECT * FROM edition WHERE base_id=?", (eid,)).fetchone()
    ch = lambda asin, seq, narr: {"asin": asin, "sequence": str(seq), "title": f"T{seq}", "subtitle": None, "author": "Tolkien",
                                  "narrator": narr, "release_date": "2020-01-01", "runtime_min": 900, "cover_url": None,
                                  "language": "english", "content_type": "Product", "format_type": "unabridged"}
    kids = [ch("S4", 4, "Andy Serkis"), ch("I4", 4, "Rob Inglis"), ch("I5", 5, "Rob Inglis, Someone")]
    audible._merge(c, v, "SER", kids)
    audible._merge(c, c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone(), "SER", kids)
    miss = lambda e: {r["asin"] for r in c.execute("SELECT asin FROM book WHERE edition_id=? AND owned=0", (e,))}
    assert miss(v["id"]) == {"I4", "I5"}
    assert miss(eid) == {"S4"}


def test_suche_und_pruefung_achten_auf_den_sprecher():
    bk = dict(title="The Two Towers", author="J.R.R. Tolkien", series="The Lord of the Rings", sequence="2", lang="en",
              runtime_min=1100, want_narr="Rob Inglis", other_narrs=["Andy Serkis"], variant="Rob Inglis")
    rel = lambda t: {"title": t, "size": 600_000_000}
    good = score(bk, rel("J.R.R. Tolkien - The Lord of the Rings 2 - The Two Towers (Rob Inglis) Unabridged"))
    other = score(bk, rel("J.R.R. Tolkien - The Lord of the Rings 2 - The Two Towers (Andy Serkis) Unabridged"))
    none = score(bk, rel("J.R.R. Tolkien - The Lord of the Rings 2 - The Two Towers Unabridged"))
    assert good["score"] > none["score"] > other["score"]
    assert "Andere Fassung" in str(other["reasons"])
    assert variants.narrator_finding(bk, "Read by Rob Inglis")["status"] == "ok"
    assert variants.narrator_finding(bk, "narrated by Andy Serkis")["status"] == "warn"
    assert variants.narrator_finding(bk, "Tolkien") is None


def test_ablage_mit_sprecher_im_ordnernamen():
    db.init()
    c = db.conn()
    _, eid = lotr(c, "Ringe Ablage")
    c.execute("UPDATE book SET rel_path='Ringe Ablage/Book '||sequence||' - '||title WHERE edition_id=?", (eid,))
    variants.assign(c)
    v = c.execute("SELECT * FROM edition WHERE base_id=?", (eid,)).fetchone()
    d, name = importer.target_rel({"edition_id": v["id"], "sequence": "4", "title": "Unfinished", "series": v["name"]})
    assert d == "Ringe Ablage" and name == "Book 4 - Unfinished [Rob Inglis]"
    # Ungebundene Hauptfassung neben einer Sprecher-Fassung: Sprecher des Buchs laut Audible
    c.execute("UPDATE edition SET narr=NULL WHERE id=?", (eid,))
    name = importer.target_rel({"edition_id": eid, "sequence": "3", "title": "Return", "narrator": "Felix von Manteuffel"})[1]
    assert name == "Book 3 - Return [Felix von Manteuffel]"
    # Einzelne Fassung ohne Geschwister: Ordnername wie bisher
    _, solo = series(c, "Ohne Fassungen")
    assert importer.target_rel({"edition_id": solo, "sequence": "1", "title": "X", "series": "Ohne Fassungen"})[1] == "Book 1 - X"


def test_trennen_legt_buecher_zurueck():
    db.init()
    c = db.conn()
    _, eid = lotr(c, "Ringe Trennen")
    variants.assign(c)
    v = c.execute("SELECT id FROM edition WHERE base_id=?", (eid,)).fetchone()["id"]
    variants.detach(c, v)
    assert c.execute("SELECT COUNT(*) FROM book WHERE edition_id=?", (eid,)).fetchone()[0] == 7
    assert c.execute("SELECT vlock, narr FROM edition WHERE id=?", (eid,)).fetchone()["vlock"] == 1
    variants.assign(c)
    assert not c.execute("SELECT 1 FROM edition WHERE base_id=?", (eid,)).fetchone()


def test_andere_buecher_unter_gleicher_nummer_sind_keine_fassung():
    db.init()
    c = db.conn()
    _, eid = series(c, "Halo Probe")
    book(c, eid, 13, "Mortal Dictata", "Euan Morton")
    book(c, eid, 11, "Thursday War", "Euan Morton")
    book(c, eid, 13, "Broken Circle", "Scott Brick")
    book(c, eid, 14, "New Blood", "Scott Brick")
    assert variants.split_plan(c, c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone()) is None


def test_hoerspiele_werden_eine_fassung():
    db.init()
    c = db.conn()
    _, eid = series(c, "Red Probe")
    for s, t in ((1, "Red Probe"), (2, "Golden Son"), (3, "Morning Star")):
        book(c, eid, s, t, "Tim Gerard Reynolds")
        book(c, eid, s, f"{t} (Part 1 of 2) (Dramatized Adaptation)", f"Sprecher{s}, Full Cast")
        book(c, eid, s, f"{t} (Part 2 of 2) (Dramatized Adaptation)", f"Anderer{s}")
    variants.assign(c)
    v = c.execute("SELECT * FROM edition WHERE base_id=?", (eid,)).fetchall()
    assert [(x["variant"], x["narr"], x["drama"]) for x in v] == [("Hörspiel", None, 1)]
    assert c.execute("SELECT COUNT(*) FROM book WHERE edition_id=?", (v[0]["id"],)).fetchone()[0] == 6
    assert variants.route(c, eid, "Wer auch immer", "Light Bringer (Dramatized Adaptation)") == v[0]["id"]


def test_gleicher_sprecher_wird_nicht_zusammengelegt():
    db.init()
    c = db.conn()
    sid, eid = series(c, "Zauber Probe")
    book(c, eid, 1, "Eins", "Stephen Fry")
    book(c, eid, 5, "Fünf", "Stephen Fry")
    fsid, feid = series(c, "Zauber Probe (Narrated by Stephen Fry)")
    book(c, feid, 1, "Eins", "Stephen Fry")
    gsid, geid = series(c, "Zauber Probe (Full-Cast Editions)")
    book(c, geid, 1, "Eins", "Hugh Laurie, Matthew Lewis")
    variants.assign(c)
    assert c.execute("SELECT series_id FROM edition WHERE id=?", (feid,)).fetchone()["series_id"] == fsid
    assert c.execute("SELECT series_id, variant FROM edition WHERE id=?", (geid,)).fetchone()["variant"] == "Full Cast"


def test_verknuepfte_karte_bleibt_wo_sie_ist():
    db.init()
    c = db.conn()
    sid, eid = series(c, "Besen Probe")
    book(c, eid, 1, "One", "Jim Dale")
    rb = c.execute("INSERT INTO edition(series_id, lang, name, norm) VALUES(?,?,?,?)",
                   (sid, "de", "Besen Probe - Gesprochen von Rufus Beck", db.norm("Besen Probe - Gesprochen von Rufus Beck"))).lastrowid
    book(c, rb, 1, "Eins", "Rufus Beck", lang="de")
    osid, oe = series(c, "Besen Probe", lang="de")  # deutsche Audible-Fassung auf eigener Karte
    c.execute("UPDATE series SET norm='andere' WHERE id=?", (osid,))
    variants.assign(c)
    assert c.execute("SELECT series_id FROM edition WHERE id=?", (rb,)).fetchone()["series_id"] == sid


def test_mehrteiliger_band_zaehlt_jeden_teil():
    db.init()
    c = db.conn()
    _, eid = series(c, "Teile Probe")
    book(c, eid, 5, "Dark Age (1 of 2) [Dramatized Adaptation]", "Full Cast")
    book(c, eid, 5, "Dark Age (2 of 2) [Dramatized Adaptation]", "Full Cast")
    ed = c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone()
    ch = lambda asin, seq, t, rt=600: {"asin": asin, "sequence": str(seq), "title": t, "subtitle": None, "author": "P", "narrator": "Full Cast",
                                       "release_date": "2020-01-01", "runtime_min": rt, "cover_url": None, "language": "english",
                                       "content_type": "Product", "format_type": "unabridged"}
    kids = [ch("L1", 6, "Light Bringer (1 of 3) [Dramatized Adaptation]"), ch("L2", 6, "Light Bringer (2 of 3) [Dramatized Adaptation]"),
            ch("L3", 6, "Light Bringer (3 of 3) [Dramatized Adaptation]"), ch("D2", 5, "Dark Age (2 of 2) [Dramatized Adaptation]"),
            ch("W7", 7, "Whole (1 of 2)"), ch("W7b", 7, "Whole", 1200)]
    audible._merge(c, ed, "SER", kids)
    miss = {r["asin"] for r in c.execute("SELECT asin FROM book WHERE edition_id=? AND owned=0", (eid,))}
    assert miss == {"L1", "L2", "L3", "W7b"}  # Teile einzeln; gibt es den Band ganz, nur ihn
    audible._merge(c, ed, "SER", kids)  # zweiter Lauf: keine Doppel
    assert c.execute("SELECT COUNT(*) FROM book WHERE edition_id=? AND owned=0", (eid,)).fetchone()[0] == 4
    assert variants.part_of("Morning Star (2 of 2) (Dramatized Adaptation)") == 2
    assert variants.part_of("Light Bringer Part 3 of 3") == 3 and variants.part_of("Book 1 of the Saga") is None


def test_teil_bewertung_und_hoerspielkennzeichen():
    bk = dict(title="Light Bringer (1 of 3) [Dramatized Adaptation]", author="Pierce Brown", series="Red Rising", sequence="6",
              lang="en", runtime_min=603, drama_series=1, variant="Hörspiel")
    s = lambda t: score(bk, {"title": t, "size": 400_000_000})
    assert s("Pierce Brown - Light Bringer (Part 1 of 3) Dramatized Adaptation")["verdict"] == "sicher"
    assert s("Pierce Brown - Light Bringer (2 of 3) [Dramatized Adaptation]")["verdict"] == "verworfen"
    assert s("Pierce Brown - Light Bringer")["verdict"] != "sicher"


def test_erwartet_den_sprecher_des_buchs():
    db.init()
    c = db.conn()
    _, eid = lotr(c, "Ringe Sprecher")
    variants.assign(c)
    shaw = c.execute("SELECT * FROM book WHERE edition_id=? AND narrator='Martin Shaw'", (eid,)).fetchone()
    ctx = variants.book_context(c, shaw)
    assert ctx["want_narr"] == "Martin Shaw" and "Rob Inglis" in ctx["other_narrs"]


def test_hoerspiel_ohne_serie_wird_fassung():
    db.init()
    c = db.conn()
    sid, eid = series(c, "Ringe Solo", lang="de")
    book(c, eid, 1, "Die Gefährten", "Achim Höppner", lang="de")
    bid = c.execute("INSERT INTO book(edition_id, lang, title, narrator, owned, abs_item_id) VALUES(NULL,'de','Der Ringe Solo. Das Hörspiel','Viele',1,'solo-1')").lastrowid
    assert variants.solo_target(c, "de", "Ringe Solo (Hörspiel)")["id"] == eid
    assert variants.solo_target(c, "de", "Ringe Solo") is None
    variants.assign(c)
    v = c.execute("SELECT * FROM edition WHERE base_id=?", (eid,)).fetchone()
    assert v["variant"] == "Hörspiel" and v["series_id"] == sid and v["drama"] == 1
    assert c.execute("SELECT edition_id FROM book WHERE id=?", (bid,)).fetchone()["edition_id"] == v["id"]
    variants.detach(c, v["id"])
    assert c.execute("SELECT edition_id FROM book WHERE id=?", (bid,)).fetchone()["edition_id"] is None
    variants.assign(c)
    assert c.execute("SELECT edition_id FROM book WHERE id=?", (bid,)).fetchone()["edition_id"] is None


def test_gewuenschter_band_bleibt_beim_abgleich():
    db.init()
    c = db.conn()
    _, eid = series(c, "Wunsch Probe")
    book(c, eid, 1, "Eins", "A")
    w = book(c, eid, 9, "Hobbit (Hörspiel)", "B", owned=0)
    c.execute("UPDATE book SET seq=NULL, sequence=NULL, wanted='x' WHERE id=?", (w,))
    audible._merge(c, c.execute("SELECT * FROM edition WHERE id=?", (eid,)).fetchone(), "SER", [])
    assert c.execute("SELECT 1 FROM book WHERE id=?", (w,)).fetchone()


def test_sprecher_auch_ohne_fassung_und_unscharf():
    # Harry Potter 3 DE (18.11.): Whisper hörte «Mantäufel», Audible nennt Felix von Manteuffel
    bk = dict(title="Harry Potter und der Gefangene von Askaban", narrator="Felix von Manteuffel", lang="de")
    heard = "Harry Potter und der Gefangene von Ascarbahn. Gelesen von Felix von Mantäufel. Kapitel eins"
    nf = variants.narrator_check(bk, [("Ansage", heard), ("Tags", ""), ("Releasename", "Rowling - Askaban 11CD")])
    assert nf["status"] == "ok" and "laut Ansage" in nf["text"]
    other = variants.narrator_check(bk, [("Ansage", "Harry Potter. Gelesen von Rufus Beck. Kapitel eins"), ("Tags", "")])
    assert other["status"] == "warn" and "Rufus Beck" in other["text"] and "Audible nennt Felix von Manteuffel" in other["text"]
    assert variants.narrator_check(bk, [("Ansage", "Kapitel eins"), ("Tags", "J.K. Rowling")]) is None
    # kurze Namen nur genau, Hörspiele ohne Erwartung
    assert variants.narrator_finding(dict(narrator="Rufus Beck"), "Back to the future") is None
    assert variants.narrator_finding(dict(title="Red Rising (Dramatized Adaptation)", narrator="Full Cast"), "read by Tim Gerard Reynolds") is None
    assert variants.narrator_finding(dict(narrator=", ".join(f"Sprecher {i}" for i in range(6))), "gelesen von Rufus Beck. Kapitel") is None
    # abgeschnittener Name am Ende der Ansage und Füllwörter nach dem Namen
    assert variants.named_narrators("Narrated by Peter Burkraut Recap. Last time") == ["Peter Burkraut"]
    assert variants.named_narrators("This is Audible. Read by Chris Gu") == []
    # Lübbe sagt «Es liest …»: Die Tore der Welt in der Kluckert-Fassung war die gekürzte Kerzel-Lesung (19.11.)
    assert variants.named_narrators("Die Tore der Welt von Ken Follett. Es liest Joachim Kerzel. November 1327.") == ["Joachim Kerzel"]
    assert variants.narrator_finding(dict(narrator="Tobias Kluckert"), "Es liest Joachim Kerzel. November 1327.")["status"] == "warn"
    assert variants.named_narrators("The Subtle Knife by Philip Pullman. Read for you by Ruth Wilson. Chapter one") == ["Ruth Wilson"]


def test_hauptfassung_neben_anderen_sprechern_braucht_den_sprecher():
    """Harry Potter 19.11.2026: Jim Dale ist die Hauptfassung, daneben gibt es Stephen Fry. Ein Treffer ohne Sprecher
    («… (English Audiobook) (book 2)», war Fry) darf nicht automatisch geladen werden; mit «Jim Dale» schon."""
    bk = dict(title="Harry Potter and the Chamber of Secrets", author="J.K. Rowling", series="Harry Potter", sequence="2", lang="en",
              runtime_min=542, want_narr="Jim Dale", other_narrs=["Stephen Fry"], variant=None)
    rel = lambda t: {"title": t, "size": 300_000_000}
    none = score(bk, rel("J.K. Rowling - Harry Potter 2 - Harry Potter and the Chamber of Secrets Unabridged"))
    good = score(bk, rel("J.K. Rowling - Harry Potter 2 - Harry Potter and the Chamber of Secrets (Jim Dale) Unabridged"))
    assert none["verdict"] != "sicher" and "nicht genannt" in str(none["reasons"])
    assert good["verdict"] == "sicher"
    # Serie ohne andere Fassung: Sprecher muss nicht genannt sein
    solo = score({**bk, "want_narr": None, "other_narrs": []}, rel("J.K. Rowling - Harry Potter 2 - Harry Potter and the Chamber of Secrets Unabridged"))
    assert solo["verdict"] == "sicher"
