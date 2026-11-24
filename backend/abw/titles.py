"""Buchtitel für die Anzeige: Audible schreibt den Seriennamen in jeden Titel, mal vorne, mal hinten, mal in Klammern
(«Tarkin: Star Wars», «Thrawn (Star Wars)», «Star Wars™ - Episode I - …»). Gespeichert bleibt der Titel unverändert."""
import re

from . import db

SEP = r"[:\-–—,]"
EXTRA = r"(?:\s+(?:Legends|Saga|Series|Universe))?"  # «Star Wars Legends» hinter dem Titel
EDITION = re.compile(r"\b(expanded|extended|unabridged|abridged|dramati[sz]ed|anniversary|special|collector'?s)\s+edition\b"
                     r"|\((?:unabridged|abridged|ungekürzt|gekürzt)\)", re.I)
# Klammern, die eine Ausgabe oder Unterreihe nennen, ohne Bandnummer: «(The High Republic)», «(A Bad Batch Novel)»
BARE_PAREN = re.compile(r"\s*\((?![^)]*\d)(?![^)]*\b(?:book|band|teil|folge|part|volume|[ivx]+)\b)[^)]*\)", re.I)
ONLY_NUMBER = re.compile(r"^(?:book|band|teil|folge|part|volume|vol\.?)?\s*[\w.]{0,4}$", re.I)
# Was nach dem Abschneiden kein eigener Titel ist: Bandangabe («Vol. 11 (light novel)», «Buch 2», «(Buch 8)», «III: Jokers Wild»,
# «9: An Isekai LitRPG»), Satzfortsetzung («und der tanzende Teufel», «and the Prisoner of Azkaban») oder nur ein Genre-Zusatz
NOT_A_TITLE = re.compile(r"^(?:[^\w]|\d|[IVXLC]+\b(?!')|(?:books?|buch|band|teil|folge|part|arc|vol(?:ume)?)\b\.?"
                         r"|(?:the\s+)?(?:complete|enhanced|deluxe|collector'?s|special|anniversary)\b)"
                         r"|litrpg|gamelit|isekai|progression fantasy|light novel|cultivation|\bvol(?:ume)?\.?\s*\d", re.I)


def _names(series: str | None) -> list[str]:
    """Seriennamen, die im Titel stehen können: ganz, der Teil vor dem Doppelpunkt («Star Wars: Legends» → «Star Wars»)
    und die Unterreihe dahinter («Star Wars: The High Republic» → «The High Republic»)."""
    s = re.sub(r"(?:\s*\([^)]*\))+\s*$", "", (series or "").replace("™", "").replace("꞉", ":"))  # «(abridged)», «(Young Adult)»
    s = re.sub(r"\s+-\s+Legends\s*$", "", s).strip()
    out = []
    head, _, tail = s.partition(":")
    for n in (s, head, s.replace(":", ""), tail if " " in tail.strip() else ""):
        n = n.strip()
        if len(n) >= 3 and n not in out:
            out.append(n)
    return sorted(out, key=len, reverse=True)


def display(title: str | None, series: str | None) -> str:
    """Titel ohne Seriennamen. Bleibt nichts Eigenes übrig («Dao of Healing, Book 1»), gilt der volle Titel."""
    full = (title or "").strip()
    t = full.replace("™", "").replace("®", "")
    for n in _names(series):
        name = r"\s+".join(map(re.escape, n.split()))
        t = re.sub(rf"\s*\(\s*{name}{EXTRA}\s*\)", "", t, flags=re.I)
        # vorne: «Star Wars: Tarkin», «Star Wars - Episode I»; ohne Trennzeichen nur bei mehreren Wörtern («Star Wars Rebel Rising»,
        # aber nicht «Dune Messiah»)
        gap = rf"(?:\s*{SEP})+\s*|\s+" if " " in n else rf"(?:\s*{SEP})+\s*"
        t = re.sub(rf"^{name}{EXTRA}(?:{gap})(?=\S)", "", t, flags=re.I)
        # hinten oder in der Mitte: «Tarkin: Star Wars», «Return of the Jedi: Star Wars: Episode VI»
        t = re.sub(rf"\s*{SEP}\s*{name}{EXTRA}(?=\s*(?:{SEP}|\(|$))", "", t, flags=re.I)
    t = re.sub(rf"^\s*{SEP}+\s*|\s*{SEP}+\s*$", "", re.sub(r"\s+", " ", t)).strip()
    if (not re.search(r"\w", t) or ONLY_NUMBER.match(t) or NOT_A_TITLE.search(t) or t[0].islower()
            or db.norm(t) in {db.norm(series), *map(db.norm, _names(series))}):
        return full
    return t


def dup_key(title: str | None, series: str | None) -> str:
    """Vergleichsschlüssel für doppelte Ausgaben desselben Buchs in einer Serie: ohne Seriennamen, Ausgabe-Zusätze
    («Expanded Edition») und Klammern ohne Bandnummer. «Thrawn Ascendancy (Book II: Greater Good)» bleibt eigen."""
    t = EDITION.sub(" ", display(title, series))
    t = BARE_PAREN.sub(" ", t)
    return db.norm(t)


GENERIC = {"untitled", "ohnetitel", "tba", "tbd", "unbenannt"}


def same_book(a, b) -> bool:
    """Zwei Einträge mit gleichem Schlüssel sind nur dann dasselbe Buch, wenn die Untertitel sich nicht widersprechen
    und die Bandnummer gleich ist (oder fehlt). Bei anderer Bandnummer muss die Laufzeit auf 3 % passen: Audible führt
    manche Ausgabe unter zwei Nummern («Battle Scars» 25 und 26), aber bei «Dragon Heart» heissen alle 14 Bände gleich."""
    sa, sb = db.norm(a["subtitle"]), db.norm(b["subtitle"])
    if sa and sb and sa != sb:
        return False
    if a["seq"] is None or b["seq"] is None or a["seq"] == b["seq"]:
        return True
    ra, rb = a["runtime_min"] or 0, b["runtime_min"] or 0
    return ra > 0 and rb > 0 and abs(ra - rb) <= 0.03 * max(ra, rb)


def collapse(books: list, states: list[str], series: str | None) -> tuple[list, list[str], dict[int, list]]:
    """Doppelte Ausgaben desselben Buchs in einer Fassung zu einem Band zusammenfassen. Es bleibt die Ausgabe im Bestand
    (sonst die am weitesten gekommene); die übrigen hängen als «auch als» an ihr. Reihenfolge und Zuordnung zu den
    Zuständen bleiben erhalten."""
    rank = {"owned": 0, "downloading": 1, "review": 2, "searching": 3, "failed": 4, "missing": 5, "upcoming": 6}
    groups: dict[str, list[list[int]]] = {}
    for i, b in enumerate(books):
        k = dup_key(b["title"], series)
        if len(k) < 4 or k in GENERIC:
            continue
        clusters = groups.setdefault(k, [])
        hit = next((cl for cl in clusters if all(same_book(books[j], b) for j in cl)), None)
        if hit is None:
            clusters.append([i])
        else:
            hit.append(i)
    drop: set[int] = set()
    extra: dict[int, list] = {}
    for idx in (cl for clusters in groups.values() for cl in clusters):
        if len(idx) < 2:
            continue
        keep = min(idx, key=lambda i: (rank.get(states[i], 9), 0 if len(books[i]["asin"] or "") == 10 else 1, i))
        extra[books[keep]["id"]] = [books[i] for i in idx if i != keep]
        drop.update(i for i in idx if i != keep)
    return ([b for i, b in enumerate(books) if i not in drop], [s for i, s in enumerate(states) if i not in drop], extra)
