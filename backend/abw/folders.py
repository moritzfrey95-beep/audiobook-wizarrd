"""Ordnerstruktur beim Ablegen: einstellbares Schema mit Platzhaltern.

Platzhalter: {author} {series} {seq} {title} {narrator} {year}. «/» trennt Ordner, der letzte Teil ist der Buchordner.
Text in <…> fällt weg, wenn ein Platzhalter darin leer ist: «<Book {seq} - >{title}» wird ohne Bandnummer nur «{title}»."""
import re

KEYS = ("author", "series", "seq", "title", "narrator", "year")
SERIES_DEFAULT = "{series}/<Book {seq} - >{title}"
SINGLE_DEFAULT = "Standalone - {author}/{title}"
# Vorlagen für Einrichtung und Einstellungen: (Serie, Einzelbuch)
PRESETS = {
    "abw": (SERIES_DEFAULT, SINGLE_DEFAULT),
    "author": ("{author}/{series}/<Book {seq} - >{title}", "{author}/{title}"),
    "author_num": ("{author}/{series}/<{seq} - >{title}", "{author}/{title}"),
}
PH = re.compile(r"\{(\w+)\}")
OPT = re.compile(r"<([^<>]*)>")


def clean(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r'[/\\\0<>|"?*]', " ", name)).strip(" .")


def problem(scheme: str, single: bool = False) -> str | None:
    """Fehler im Schema als Text, sonst None."""
    s = (scheme or "").strip()
    if not s:
        return "Das Schema ist leer"
    if bad := [k for k in PH.findall(s) if k not in KEYS]:
        return f"Unbekannter Platzhalter: {{{bad[0]}}}"
    if re.sub(r"<[^<>]*>", "", s).count("<") or re.sub(r"<[^<>]*>", "", s).count(">"):
        return "Spitze Klammern < > müssen paarweise stehen und dürfen nicht verschachtelt sein"
    if s.startswith("/") or any(p.strip() in ("..", ".") for p in s.split("/")):
        return "Das Schema muss relativ zur Bibliothek sein, ohne «..»"
    last = _segments(s)[-1]
    if "{title}" not in OPT.sub("", last):
        return "Der letzte Ordner muss {title} ausserhalb von < > enthalten, sonst stossen Bücher zusammen"
    if not single and "{series}" not in s and "{seq}" not in s:
        return "Für Serien muss {series} oder {seq} vorkommen"
    return None


BOOK_KEYS = {"seq", "title", "narrator", "year"}  # gehören zum einzelnen Band, nicht zur Serie oder zum Autor


def _segments(scheme: str) -> list[str]:
    """Ordnerteile des Schemas; ein «/» in < > zählt mit zum Teil davor (fällt mit ihm weg)."""
    out, depth, cur = [], 0, ""
    for ch in scheme.strip():
        depth += {"<": 1, ">": -1}.get(ch, 0)
        if ch == "/" and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    return [*out, cur]


def split(scheme: str, vals: dict) -> tuple[list[str], list[str]]:
    """Ordnerteile zum Schema, getrennt in Gruppe (Serie, Autor) und Band. Leere Teile fallen weg."""
    v = {k: str(vals.get(k) or "").strip() for k in KEYS}
    fill = lambda text: PH.sub(lambda m: v.get(m.group(1), ""), text)
    opt = lambda text: OPT.sub(lambda m: fill(m.group(1)) if all(v.get(k) for k in PH.findall(m.group(1))) else "", text)
    head, tail = [], []
    for seg in _segments(scheme):
        target = tail if tail or BOOK_KEYS & set(PH.findall(seg)) else head
        target.extend(p for p in (clean(fill(x)) for x in opt(seg).split("/")) if p)
    if not tail:
        tail = [clean(v["title"]) or "Unbenannt"]
    return head, tail


def render(scheme: str, vals: dict) -> tuple[str, str]:
    """Ordner und Buchordner zum Schema."""
    head, tail = split(scheme, vals)
    parts = head + tail
    return "/".join(parts[:-1]), parts[-1]


def volume_dir(scheme: str) -> re.Pattern | None:
    """Muster für einen eigenen Ordner je Band («…/Book 2/Titel»), sonst None."""
    segs = _segments(scheme)
    if not any("{seq}" in x for x in segs[:-1]) and "/" not in segs[-1].replace(">", "").split("{title}")[0]:
        return None
    return volume_prefix(scheme, None)


def volume_prefix(scheme: str, seq: str | None) -> re.Pattern | None:
    """Muster für einen Ordner derselben Bandnummer nach diesem Schema: «Book 2» trifft nicht «Book 2.5» oder «Book 21».
    Ohne Nummer: irgendeine Bandnummer."""
    seg = next((x for x in _segments(scheme) if "{seq}" in x), None)
    if seg is None:
        return None
    raw = seg.replace("<", "").replace(">", "").split("/")
    before = next(x for x in raw if "{seq}" in x).split("{seq}", 1)[0]
    if PH.search(before):  # etwas wie «{series} {seq}»: nicht sicher erkennbar
        return None
    num = rf"0*{re.escape(seq)}(?![\d.])" if seq is not None else r"\d+(\.\d+)?\b"
    return re.compile(rf"^{re.escape(before.lstrip())}{num}")


SAMPLES = [
    {"author": "Brandon Sanderson", "series": "Mistborn", "seq": "2", "title": "The Well of Ascension", "narrator": "Michael Kramer", "year": "2007"},
    {"author": "Terry Pratchett", "series": "Discworld", "seq": "", "title": "The Last Hero", "narrator": "Stephen Briggs", "year": "2001"},
]
SINGLE_SAMPLE = {"author": "Andy Weir", "title": "Project Hail Mary", "narrator": "Ray Porter", "year": "2021"}


def preview(series: str, single: str) -> dict:
    err = problem(series) or problem(single, single=True)
    if err:
        return {"ok": False, "text": err, "examples": []}
    ex = ["/".join(p for p in render(series, s) if p) for s in SAMPLES]
    ex.append("/".join(p for p in render(single, SINGLE_SAMPLE) if p))
    return {"ok": True, "text": "", "examples": ex}
