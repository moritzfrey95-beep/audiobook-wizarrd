"""Bewertung eines Suchtreffers gegen ein gesuchtes Buch: Punktzahl 0-100 mit Begründung."""
import re
import unicodedata

STOP = frozenset("the a an of and in to for at on der die das des dem den ein eine und von zu im am book band volume vol teil".split())
# «ungekrzt»: Umlaut beim Hochladen verloren. «Sammelpack», «Folge»: nur in deutschen Releases üblich
GERMAN = frozenset("german deutsch ger hoerbuch horbuch ungekurzt gekurzt ungekuerzt gekuerzt ungekrzt gekrzt sammelpack sammelband sammelbox "
                   "gesamtausgabe komplettausgabe komplettpaket gesamtpaket folge folgen".split())
# Kleine deutsche Wörter im Releasenamen: «Beren und Lúthien» ist die deutsche Ausgabe von «Beren and Lúthien».
# Ohne «die», «den», «des», «von»: kommen in englischen Titeln und Namen vor
GERMAN_SMALL = frozenset("und das dem der eine vom zum zur".split())
ENGLISH = frozenset("book books unabridged abridged narrated adventure novel".split())  # in deutschen Releases: Band, Teil, Folge, ungekürzt
FOREIGN = {"svensk": "Schwedisch", "se": "Schwedisch", "espanol": "Spanisch", "spanish": "Spanisch", "es": "Spanisch",
           "french": "Französisch", "francais": "Französisch", "fr": "Französisch", "italian": "Italienisch",
           "italiano": "Italienisch", "dutch": "Niederländisch", "nl": "Niederländisch", "polish": "Polnisch",
           "pl": "Polnisch", "utgaava": "Schwedisch", "neutro": "Spanisch"}
NOT_AUDIO = re.compile(r"\b(epub|pdf|mobi|azw3?|ebook|cbz|cbr|retail epub)\b", re.I)
DRAMA = re.compile(r"\b(graphic ?audio|immersion tunnel|dramati[sz]ed|dramati[sz]ation|horspiel|hoerspiel|full cast|bbc|radio (?:drama|play)|drama|adapted)\b", re.I)
COUNT_WORD = r"(?:\d{1,2}|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|zwei|drei|vier|funf|sechs|sieben|acht|neun|zehn)"
# Begriffe, die auf mehrere Bände in einem Download deuten (Auswertung der Downloads 11.-17.11.2026)
MULTI_WORD = re.compile(
    r"\b(complete|collection|box ?set|boxed set|omnibus|sammlung|gesamtausgabe|"
    r"trilog(?:y|ie)|tetralog(?:y|ie)|quadrilog(?:y|ie)|pentalog(?:y|ie)|hexalog(?:y|ie)|duolog(?:y|ie)|dilogy|"
    r"sammelpack|sammelband|sammelbox|gesamtpaket|komplettpaket|komplettausgabe|"
    r"(?:complete|full|entire|whole) series|series complete|gesamte serie|komplette serie|komplette reihe|"
    r"batch|bundle|anthology|anthologie|compilation|"
    r"all " + COUNT_WORD + r" (?:books|novels|volumes|parts)|alle " + COUNT_WORD + r" (?:bande|bucher|teile|folgen)|"
    r"\d{1,2}[ -]in[ -]1)\b", re.I)
MULTI_RANGE = re.compile(r"\b(?:books?|band|bande|vol(?:ume)?s?|folgen?)\s*0*(\d{1,3})\s*(?:-|to|bis|&)\s*0*(\d{1,3})\b", re.I)
NUMBER = re.compile(r"(?:\b(?:book|bk|band|vol(?:ume)?|teil|bd|part|folge)\.?\s*|#\s*)0*(\d{1,3}(?:\.\d)?)\b", re.I)


def multi_range(p: str) -> bool:
    """'Books 1-5', 'Vol 6 - 8'; nicht 'Book 2 - 1-800-Starship' oder 'Book 9 - 86 Eighty-Six' (dort folgt nach dem Strich der Titel)."""
    return any(0 < int(a) < int(b) <= int(a) + 50 for a, b in MULTI_RANGE.findall(p))


def is_multi(p: str) -> bool:
    return multi_range(p) or bool(MULTI_WORD.search(p))


def title_numbers(title: str, series: str = "") -> set[str]:
    """Nummern, die zum Titel gehören ('Dungeon Diving 101'): ab drei Stellen, keine Jahreszahl, nicht aus dem Seriennamen."""
    own = set(re.findall(r"\d+", plain(series)))
    t = re.sub(r"\b\d+\s*(?:kbps|kbit|k|cd|mb|gb|hrs?|h|min)\b", " ", plain(re.sub(r"\(.*?\)", "", title)))
    return {n.lstrip("0") or "0" for n in re.findall(r"(?<![\d.])\d{3,4}(?![\d.])", t) if n not in own and not re.fullmatch(r"(?:19|20)\d{2}", n)}


def plain(text: str | None) -> str:
    t = unicodedata.normalize("NFKD", (text or "").lower().replace("ß", "ss"))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"(?<!\d)\.|\.(?!\d)|_", " ", t)  # Punkte trennen Wörter, Dezimalnummern bleiben
    return re.sub(r"[^a-z0-9.#&-]+", " ", t.replace("’", "").replace("'", "")).strip()


def words(text: str | None) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", plain(text)) if w]


def significant(text: str | None) -> set[str]:
    return {w for w in words(text) if w not in STOP and not w.isdigit()}


def coverage(wanted: set[str], have: set[str]) -> float:
    return len(wanted & have) / len(wanted) if wanted else 0.0


def num_key(n: str) -> str:
    """'03' wird '3', '0.5' bleibt '0.5' (nicht '.5'), '2.0' wird '2' wie bei seq_key."""
    whole, dot, frac = n.partition(".")
    return ((whole.lstrip("0") or "0") + dot + frac).removesuffix(".0")


def numbers(release: str, series: str = "") -> set[str]:
    """Ausdrückliche Bandnummern: 'Book 8', '#4', 'Band 03' und zweistellige '02' direkt nach dem Seriennamen."""
    p = plain(release)
    found = {num_key(n) for n in NUMBER.findall(p)}
    key = " ".join(words(series))
    if key:
        m = re.search(re.escape(key) + r"\s+0*(\d{1,3}(?:\.\d)?)\b(?!\s*(?:kbps|k|cd|mb|gb))", " ".join(re.split(r"[^a-z0-9.]+", p)))
        if m:
            found.add(num_key(m.group(1)))
    return found


RANGE_TAIL = frozenset("mp3 m4b m4a flac audiobook audio abook ddc unabr unabridged ungekurzt german deutsch de en complete wav".split())
SPAN = r"0*(\d{1,3})\s*(?:-|to|bis|&)\s*0*(\d{1,3})\b(?!\s*(?:kbps|kbit|k|cd|mb|gb|hrs?|h|min))"


def span(release: str, series: str = "") -> tuple[int, int] | None:
    """Sammelband: 'Books 1-3', 'Vol 14 to 16' oder 'Serie 14-16' ergibt (14, 16)."""
    p = plain((release or "").replace("–", "-").replace("—", "-"))
    pats = [r"(?:\b(?:books?|bande?|vol(?:ume)?s?|teile?|bd|parts?|folgen?)\.?\s*|#\s*)" + SPAN]
    key = words(series)
    if key:
        pats.append(r"\b" + r"[\s-]+".join(map(re.escape, key)) + r"[\s,-]+" + SPAN)
    for pat in pats:
        m = re.search(pat, p)
        if m and 0 < int(m.group(1)) < int(m.group(2)) <= int(m.group(1)) + 20:
            return int(m.group(1)), int(m.group(2))
    # Bereich ohne Stichwort: «Planetfall.1-4.2015», «Primal Hunter 01 - 07-AUDiOBOOK», «Series 1-9»; nicht «Resident Evil 2 - 3 Wishes»
    for m in re.finditer(r"\b[a-z]{3,}[\s,-]+0*(\d{1,2})\s*(?:-|to|bis)\s*0*(\d{1,2})\b(?!\s*(?:kbps|kbit|k|cd|mb|gb|hrs?|h|min))", p):
        a, b = int(m.group(1)), int(m.group(2))
        rest = p[m.end():].lstrip(" -")
        nxt = re.match(r"[a-z]+", rest)
        if 0 < a < b <= a + 30 and (not nxt or nxt.group(0) in RANGE_TAIL):
            return a, b
    # «The complete Set (12 books)»: Band 1 bis 12
    m = re.search(r"\b(\d{1,2})\s*(?:books|bande|bucher|novels)\b", p)
    if m and 2 <= int(m.group(1)) <= 20 and MULTI_WORD.search(p):
        return 1, int(m.group(1))
    return None


def titled_span(release: str, seq_titles: dict[str, str], series: str = "") -> tuple[int, int] | None:
    """Sammelband, der seine Bände nur mit Titeln nennt («Worlds of Exile and Illusion: Rocannon's World; Planet of Exile;
    City of Illusions»): stehen die Titel von mindestens zwei aufeinanderfolgenden Bänden vollständig im Treffer, ist das
    der Bereich. Es zählen nur unverwechselbare Titel: mindestens zwei eigene Wörter (ohne Seriennamen und Zahlen), und
    kein anderer Band der Serie enthält dieselben Wörter («Rift Magus Reborn» steckt in «Rift Magus Reborn 2»,
    «Apocalypse: Quest System» und «Apocalypse: Dungeon System» teilen sich zu viel). Liefert nichts bei Lücken."""
    have, ser = set(words(release)), significant(series)
    sigs = {k: significant(t) - ser for k, t in (seq_titles or {}).items() if k.isdigit()}
    found = set()
    for k, sig in sigs.items():
        if len(sig) < 2 or any(o != k and sig & other and (sig <= other or len(sig & other) * 2 >= len(sig))
                               for o, other in sigs.items()):
            continue
        last = (words(seq_titles[k]) or [""])[-1]
        if re.search(rf"\b{re.escape(last)}\s+0*\d{{1,3}}\b", plain(release)):
            continue  # «[Pillars of the Earth 02]», «Weirdest Noob 02»: der Titel von Band 1 steht hier als Serienname
        if sig <= have:
            found.add(int(k))
    if len(found) < 2 or max(found) - min(found) + 1 != len(found):
        return None
    return min(found), max(found)


def bare_numbers(release: str, known: str) -> set[str]:
    """Allein stehende kleine Zahlen im Release ("LitRPG 1 - Autor"), die nicht schon zu Titel oder Serie gehören."""
    own = set(re.findall(r"\d+", plain(known)))
    p = re.sub(r"\b\d+\s*(?:kbps|kbit|k|cd|mb|gb|hrs?|h|min)\b|\b(?:19|20)\d{2}\b|\bmp3\b|\bm4[ab]\b", " ", plain(release))
    return {n.lstrip("0") or "0" for n in re.findall(r"(?<![\d.#])\b0*(\d{1,2})\b(?![\d.])", p)} - {n.lstrip("0") or "0" for n in own}


def seq_key(sequence: str | None) -> str | None:
    if not sequence:
        return None
    m = re.match(r"^\s*0*(\d+(?:\.\d)?)\s*$", sequence)
    return (m.group(1).removesuffix(".0")) if m else None


def edition_marker(series: str | None) -> str | None:
    """Serie, die eine bestimmte Fassung meint: «Harry Potter (Narrated by Stephen Fry)», «… (gelesen von X)», «BBC Radio's …».
    Liefert den Zusatz, der im Treffer stehen muss (Sprecher oder «BBC»), sonst None."""
    m = re.search(r"\((?:narrated|read|performed) by ([^)]+)\)|\(gelesen von ([^)]+)\)", series or "", re.I)
    if m:
        return (m.group(1) or m.group(2)).strip()
    return "BBC" if re.search(r"\bBBC\b", series or "") else None


def score(book: dict, release: dict, siblings: list[str] = ()) -> dict:
    """book: title, author, series, sequence, lang, runtime_min. release: title, size."""
    name = release["title"]
    marker = edition_marker(book.get("series"))
    book = {**book, "series": re.sub(r"\s*(?:\([^)]*\)|\[[^\]]*\])", "", book.get("series") or "").strip()}
    # Teil eines mehrteiligen Bandes («Light Bringer (1 of 3)»): die Teilnummer ist keine Bandnummer, sie zählt unten für sich
    from . import variants
    bpart, rpart = variants.part_of(book["title"]), variants.part_of(name)
    if bpart is not None or rpart is not None:
        name = variants.PART.sub(" ", name)
        book["title"] = variants.PART.sub(" ", book["title"]).strip()
    have = set(words(name))
    reasons: list[tuple[int, str]] = []
    add = lambda pts, text: reasons.append((pts, text))

    if NOT_AUDIO.search(plain(name)):
        return {"score": 0, "verdict": "verworfen", "reasons": [{"points": -100, "text": "Kein Hörbuch (E-Book oder Comic)"}]}

    full, head = significant(book["title"]), significant(re.split(r"[:(\[]| - ", book["title"])[0])
    series = significant(book.get("series"))
    surnames = {w[-1] for a in (book.get("author") or "").split(",") if (w := words(a))}
    author_ok = bool(surnames & have)
    # Kurzform vor dem Doppelpunkt zählt nur, wenn sie mehr ist als der Serienname ("Star Wars: Convergence"),
    # kein anderer Band genauso beginnt ("Apocalypse: Fairy System") und sie nicht nur ein einzelnes Wort ohne Autor ist
    sib_heads = {frozenset(significant(re.split(r"[:(\[]| - ", s)[0])) for s in siblings if s != book["title"]}
    head_ok = bool(head - series) and frozenset(head) not in sib_heads and (len(head) >= 2 or author_ok)
    cov = max(coverage(full, have), coverage(head, have) if head_ok else 0)
    title_is_series = bool(full) and full <= series
    if cov >= 0.5:
        add(round(40 * cov), "Titel passt" if cov >= 0.8 else "Titel passt teilweise")
    else:
        add(0, "Titel passt nicht")

    add(20 if author_ok else 0, "Autor genannt" if author_ok else "Autor nicht genannt")

    if series and coverage(series, have) >= 0.8:
        add(10, "Serienname genannt")
    want = seq_key(book.get("sequence"))
    nums = numbers(name, book.get("series") or "")
    # Sammelband "Serie 14-16": enthält er den gesuchten Band, lässt er sich nach dem Download aufteilen
    sp = span(name, book.get("series") or "")
    by_titles = False
    if not sp and (sp := titled_span(name, book.get("seq_titles") or {}, book.get("series") or "")):
        by_titles = True
    bundle = sp if sp and want and want.isdigit() and sp[0] <= int(want) <= sp[1] else None
    known = book.get("seq_runtimes")
    unsplittable = bool(bundle) and known is not None and not all(known.get(str(n)) for n in range(sp[0], sp[1] + 1))
    if unsplittable:
        bundle = None  # nicht alle Bände im Katalog bekannt: liesse sich nach dem Download nicht aufteilen
    # Nummer im Titel ('Dungeon Diving 101' ist Band 1): eigene zählt wie der Titel, die eines anderen Bandes verrät ihn
    tnums = title_numbers(book["title"], book.get("series") or "")
    sib_tnums = {n: s for s in siblings if s != book["title"] for n in title_numbers(s, book.get("series") or "") - tnums}
    in_release = title_numbers(name)
    title_num_ok = bool(tnums) and tnums <= in_release
    other_tnum = next((sib_tnums[n] for n in sorted(in_release) if n in sib_tnums), None)
    nums -= tnums | set(sib_tnums)
    if want and sp:
        nums = set()
    elif want and not nums:
        # Keine ausdrückliche Angabe wie "Book 2": dann zählen allein stehende Zahlen
        nums = bare_numbers(name, f"{book['title'].replace('(Book ' + want + ')', '')} {book.get('series') or ''}")
    # Gleich heissen Bände auch mit verschiedener kleiner Zahl («Narr 3», «Narr 4»), nicht mit eigener Titelnummer («Dungeon Diving 101», «102»)
    ident = lambda t: significant(re.sub(r"\(.*?\)", "", t)) | title_numbers(t, book.get("series") or "")
    twins = [s for s in siblings if ident(s) == ident(book["title"])]
    number_ok = False
    if other_tnum:
        add(-40, f"Titel eines anderen Bandes: {other_tnum[:40]}")
    elif bundle:
        number_ok = True
        how = "nennt die Titel der Bände" if by_titles else "Band"
        add(10, f"Sammelband {how} {sp[0]}–{sp[1]}, enthält Band {want}: wird nach dem Download in Einzelbände aufgeteilt")
    elif unsplittable:
        add(-40, f"Sammelband Band {sp[0]}–{sp[1]}: nicht alle Bände sind im Katalog bekannt, Aufteilen wäre nicht möglich")
    elif want and sp:
        add(-40, f"Sammelband Band {sp[0]}–{sp[1]}, Band {want} ist nicht dabei")
    elif want and nums:
        if want in nums:
            number_ok = True
            add(20, f"Bandnummer {want} passt")
        else:
            add(-40, f"Andere Bandnummer ({', '.join(sorted(nums))} statt {want})")
    elif title_num_ok:
        number_ok = True
        add(20, f"Nummer im Titel passt ({', '.join(sorted(tnums))})")
    if bundle and cov < 0.8 and series and coverage(series, have) >= 0.8 and author_ok:
        # Sammelband nennt Einzeltitel selten: Serie, Autor und Bereich mit dem Band bestimmen ihn eindeutig
        cov = 1.0
        reasons[0] = (40, "Serie und Autor passen, der Bereich enthält den Band")
    # Nur wenn mehr als der Serienname passt oder die Serie selbst genannt ist: «Legends of Dune, Bk 1» ist nicht «Dune: House Atreides»
    if cov < 0.8 and number_ok and head and coverage(head, have) == 1 \
            and (head - series or not series or coverage(series, have) >= 0.8) \
            and (author_ok or full <= head or want in words(re.split(r"[:(\[]| - ", book["title"])[0])):
        # "Serie, Book 2": Serientitel plus passende Bandnummer benennt den Band eindeutig
        cov = 1.0
        reasons[0] = (40, "Serientitel und Bandnummer passen")
    other = next((s for s in siblings if s != book["title"] and (sig := significant(re.split(r"[:(\[]| - ", s)[0]) - series - head)
                  and len(sig) >= 2 and len(sig & have) == len(sig)), None)
    if not other:
        # ganzer Titel eines anderen Bandes steht im Treffer und nennt Wörter, die der gesuchte Titel nicht hat
        other = next((s for s in siblings if s != book["title"] and (sig := significant(s)) and sig <= have and sig - series - full), None)
    if other and not number_ok:
        add(-40, f"Titel eines anderen Bandes: {other[:40]}")
    elif twins and not number_ok:
        add(-30, "Die Bände dieser Serie heissen gleich, und die Bandnummer ist im Treffer nicht erkennbar")
    elif title_is_series and not number_ok and want not in (None, "1"):
        add(-15, "Titel gleich Serienname, Bandnummer nicht erkennbar")

    # "DE" zählt nur als Szene-Kürzel ("-AUDiOBOOK-DE-2024"), nicht als spanisches oder französisches Wort
    german = bool(GERMAN & have) or release.get("language") == "de" or bool(re.search(r"[-.\[(]DE[-.\])]", name))
    joined = " ".join(sorted(have))
    foreign = next((v for k, v in FOREIGN.items() if k not in full and (k in have or (len(k) >= 6 and k in joined))), None)
    if foreign:
        add(-60, f"Andere Sprache: {foreign}")
    elif book["lang"] == "en" and german:
        add(-40, "Deutsche Ausgabe, gesucht ist Englisch")
    elif book["lang"] == "en" and (de_small := sorted(GERMAN_SMALL & have - set(words(" ".join(str(book.get(k) or "") for k in ("title", "series", "author")))))):
        add(-40, f"Deutsche Wörter im Namen ({', '.join(de_small)}), gesucht ist Englisch")
    elif book["lang"] == "de" and german:
        add(10, "Als deutsch gekennzeichnet")
    elif book["lang"] == "de" and book.get("same_title_other_lang"):
        add(-30, "Titel wie in der englischen Fassung und nicht als deutsch gekennzeichnet")
    elif book["lang"] == "de" and (eng := sorted(ENGLISH & have - set(words(book["title"] + " " + (book.get("series") or ""))))):
        # «Syl: Diffusion, Book 3 - Lunadea [M4B]» für die deutsche Ausgabe: die Serien beider Sprachen sind nicht immer verknüpft
        add(-30, f"Englische Angaben im Namen ({', '.join(eng)}) und nicht als deutsch gekennzeichnet")
    elif book["lang"] == "de":
        add(-5, "Sprache nicht gekennzeichnet")

    p = plain(name)
    if DRAMA.search(p) and not book.get("drama_series") and not DRAMA.search(plain(book["title"] + " " + (book.get("series") or ""))):
        add(-25, "Hörspiel- oder Sonderfassung")
    if book.get("want_narr"):
        # Sprecher-Fassung: Sprecher genannt gibt Punkte, ein Sprecher einer anderen Fassung zieht ab
        last = lambda n: (words(n) or [""])[-1]
        other = next((o for o in book.get("other_narrs") or [] if last(o) and last(o) in have and last(o) != last(book["want_narr"])), None)
        if last(book["want_narr"]) in have:
            add(10, f"Sprecher genannt ({book['want_narr']})")
        elif other:
            add(-30, f"Andere Fassung: gelesen von {other}, gesucht ist {book['want_narr']}")
        elif book.get("variant") or book.get("other_narrs"):
            # auch die Hauptfassung, wenn es Fassungen anderer Sprecher gibt (Harry Potter 19.11.: «(English Audiobook)
            # (book 2)» war Stephen Fry statt Jim Dale): ohne Sprecher nie automatisch, nur unter «Treffer wählen»
            add(-25, f"Gesucht ist die Fassung von {book['want_narr']}, der Sprecher ist im Treffer nicht genannt")
        marker = None
    if marker:
        # bestimmte Fassung gesucht: ohne Sprecher bzw. «BBC» im Treffer ist es vermutlich eine andere
        if words(marker)[-1] in have:
            add(10, f"Fassung genannt ({marker})")
        else:
            add(-25, f"Gesucht ist die Fassung «{marker}», sie ist im Treffer nicht genannt")
    if re.search(r"(?<!un)\babridged\b|(?<!un)\bgek(?:ue|u)?rzt\b", p):
        # nie automatisch: höchstens «unsicher», auch wenn sonst alles passt
        add(-25, "Gekürzte Fassung")
    # «The Complete Story … Book 5 - Order of the Phoenix» ist ein Einzelband aus einer Reihe: nur das Wort zählt dann nicht
    single = number_ok and cov >= 0.8 and not sp and not multi_range(p)
    # Gesamtausgabe dieser Serie ohne Bandangabe («Complete Series», Autor genannt): enthält den Band vermutlich.
    # Bewusst unsicher statt verworfen, nie automatisch; die Ankunftsprüfung erkennt den Sammelband an der Laufzeit
    gesamt = not bundle and not sp and not multi_range(p) and bool(MULTI_WORD.search(p)) and not is_multi(plain(book["title"])) \
        and not single and bool(series) and coverage(series, have) >= 0.8 and author_ok and not other \
        and not (want and nums and want not in nums)
    if gesamt:
        if reasons[0][0] < 30:
            reasons[0] = (30, "Gesamtausgabe der Serie, der Titel des Bandes steht nicht im Treffer")
        add(0, "Gesamtausgabe ohne Bandangabe: wird nach dem Download an der Laufzeit erkannt und aufgeteilt, nie automatisch geladen")
    elif not bundle and is_multi(p) and not is_multi(plain(book["title"])) and not single:
        add(-30, "Sammelband mit mehreren Büchern")

    size, runtime = release.get("size") or 0, book.get("runtime_min") or 0
    if bundle:
        # Grösse und Laufzeit gelten für alle Bände des Sammelbands zusammen
        parts = [(book.get("seq_runtimes") or {}).get(str(n)) for n in range(sp[0], sp[1] + 1)]
        runtime = sum(parts) if all(parts) else 0
    whole = runtime
    if size and runtime:
        rate = size / 1e6 / runtime
        if rate < 0.15:
            add(-40, f"Zu klein für {runtime // 60} h Laufzeit ({round(size / 1e6)} MB)")
        elif rate < 0.25 or rate > 4:
            add(-10, "Grösse ungewöhnlich für die Laufzeit")
        else:
            add(10, "Grösse passt zur Laufzeit")

    if release.get("language") == "en" and book["lang"] == "de":
        add(-60, "Die Quelle nennt Englisch als Sprache")
    if release.get("runtime_min") and whole:
        diff = release["runtime_min"] - whole
        if abs(diff) <= max(5, whole * 0.03):
            add(10, "Laufzeit laut Quelle passt zu Audible")
        elif abs(diff) > 60:
            add(-40, f"Laufzeit laut Quelle weicht um {abs(diff)} Minuten ab")
    if release.get("links_unclear"):
        add(-30, "Mehrere Download-Links, Zuordnung unklar")

    # AudioBookBay meldet immer "1 Seeder": die Zahl sagt nichts und zählt nicht
    if "seeders" in release and release.get("source") != "audiobookbay":
        if not release["seeders"]:
            add(-40, "Keine Seeder, der Torrent ist wahrscheinlich tot")
        elif release["seeders"] < 3:
            add(-5, f"Nur {release['seeders']} Seeder")

    if bpart is not None:
        if rpart == bpart:
            add(10, f"Teil {bpart} passt")
        elif rpart is not None:
            add(-40, f"Anderer Teil ({rpart} statt {bpart})")
        else:
            add(-15, f"Teil {bpart} ist im Treffer nicht genannt")
    elif rpart is not None:
        add(-30, f"Nur Teil {rpart} eines Bandes")
    if book.get("variant") in variants.DRAMA_LABELS and not DRAMA.search(p):
        add(-30, f"Gesucht ist die Fassung «{book['variant']}», der Treffer ist nicht als Hörspiel gekennzeichnet")

    total = max(0, min(100, sum(pts for pts, _ in reasons)))
    heavy = any(pts <= -25 for pts, _ in reasons)
    if total >= 70 and cov >= 0.8 and not heavy and (author_ok or number_ok) and not gesamt:
        verdict = "sicher"  # gilt auch für Sammelbände: sie werden nach dem Download aufgeteilt
    elif total >= 40 and not any(pts <= -40 for pts, _ in reasons):
        verdict = "unsicher"
    else:
        verdict = "verworfen"
    return {"score": total, "verdict": verdict, "bundle": list(bundle) if bundle else None,
            "reasons": [{"points": pts, "text": text} for pts, text in reasons]}
