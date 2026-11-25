"""Autoren zusammenführen. Audible (und damit Audiobookshelf) führt denselben Autor teils unter mehreren Schreibweisen
(«J.R.R. Tolkien», «J. R. R. Tolkien», «John Ronald Reuel Tolkien»). Eine zusammengeführte Schreibweise zeigt überall auf den
geführten Namen: Serienkarten, Autorenliste, Markierungen, Statistik. Der Autorenkatalog fragt Audible unter allen Namen ab."""
import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone

from . import abs as abs_mod
from . import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def raw_name(key: str) -> str | None:
    """Name eines Autors so, wie er in den Büchern steht (ohne Zusammenführung), zum Schlüssel."""
    for r in db.conn().execute("SELECT DISTINCT author FROM book WHERE author IS NOT NULL"):
        n = abs_mod.primary_author(r["author"], resolve=False)
        if db.norm(n) == key:
            return n
    return None


def known_names() -> list[str]:
    """Alle Autorennamen aus den Büchern (ohne Zusammenführung), für die Suche nach Doppelten."""
    out = {}
    for r in db.conn().execute("SELECT DISTINCT author FROM book WHERE author IS NOT NULL AND (owned=1 OR wanted IS NOT NULL OR edition_id IS NOT NULL)"):
        n = abs_mod.primary_author(r["author"], resolve=False)
        if n != "Unbekannt":
            out.setdefault(db.norm(n), n)
    return list(out.values())


def suggestions() -> list[dict]:
    """Mögliche Doppelte, die noch nicht zusammengeführt sind."""
    alias = abs_mod.author_aliases()
    out = []
    for a, b in duplicates(known_names()):
        if db.norm(a) in alias or db.norm(b) in alias:
            continue
        out.append({"a": {"key": db.norm(a), "name": a}, "b": {"key": db.norm(b), "name": b}})
    return out


def names_of(target_key: str) -> list[str]:
    """Weitere Schreibweisen eines geführten Autors."""
    return [r["alias_name"] for r in db.conn().execute("SELECT alias_name, target_name FROM author_alias ORDER BY alias_name")
            if db.norm(r["target_name"]) == target_key]


def merge(alias_key: str, alias_name: str, target_name: str) -> None:
    """alias_key wird künftig als target_name geführt. Markierungen ziehen mit, der Katalog wird neu abgefragt."""
    target_key = db.norm(target_name)
    if not alias_key or alias_key == target_key:
        raise ValueError("Ein Autor lässt sich nicht mit sich selbst zusammenführen")
    c = db.conn()
    # Wer bisher selbst Ziel war, zeigt jetzt mit auf das neue Ziel (keine Ketten)
    c.execute("UPDATE author_alias SET target_name=? WHERE target_name=?", (target_name, alias_name))
    for r in c.execute("SELECT alias_norm, target_name FROM author_alias").fetchall():
        if db.norm(r["target_name"]) == alias_key:
            c.execute("UPDATE author_alias SET target_name=? WHERE alias_norm=?", (target_name, r["alias_norm"]))
    c.execute("INSERT OR REPLACE INTO author_alias(alias_norm, alias_name, target_name, created) VALUES(?,?,?,?)",
              (alias_key, alias_name, target_name, _now()))
    for table in ("follow", "auto_follow"):
        c.execute(f"INSERT OR IGNORE INTO {table}(user_id, kind, key, created) SELECT user_id, kind, ?, created FROM {table}"
                  " WHERE kind='author' AND key=?", (target_key, alias_key))
        c.execute(f"DELETE FROM {table} WHERE kind='author' AND key=?", (alias_key,))
    c.execute("DELETE FROM author_catalog WHERE author_norm=?", (alias_key,))
    c.execute("DELETE FROM author_fetch WHERE author_norm IN (?, ?)", (alias_key, target_key))
    abs_mod.reset_aliases()


def split(alias_key: str) -> bool:
    c = db.conn()
    row = c.execute("SELECT target_name FROM author_alias WHERE alias_norm=?", (alias_key,)).fetchone()
    if not row:
        return False
    c.execute("DELETE FROM author_alias WHERE alias_norm=?", (alias_key,))
    c.execute("DELETE FROM author_fetch WHERE author_norm=?", (db.norm(row["target_name"]),))
    abs_mod.reset_aliases()
    return True


def _parts(name: str) -> list[str]:
    """Namensteile klein und ohne Akzente; anders als db.norm bleiben Initialen wie «A.» erhalten."""
    t = unicodedata.normalize("NFKD", name.lower().replace("ß", "ss"))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return [p for p in re.split(r"[^a-z0-9]+", t) if p]


def duplicates(names: list[str]) -> list[list[str]]:
    """Mögliche Doppelte: gleicher Nachname, Vornamen passen als Initialen zueinander («J R R» zu «John Ronald Reuel»)."""
    groups = defaultdict(set)
    for n in names:
        p = _parts(n)
        if len(p) >= 2:
            groups[p[-1]].add(n)
    out = []
    for last, group in groups.items():
        group = sorted(group)
        seen = set()
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if db.norm(a) == db.norm(b) or (a, b) in seen:
                    continue
                if _compatible(_parts(a)[:-1], _parts(b)[:-1]):
                    out.append([a, b])
                    seen.add((a, b))
    return out


def _compatible(x: list[str], y: list[str]) -> bool:
    """Vornamen gleich, wenn jeder Teil gleich ist oder einer die Initiale des anderen («j» zu «john»).
    «JRR» als ein Teil zählt wie «J R R»."""
    def expand(p):
        return [ch for ch in p[0]] if len(p) == 1 and len(p[0]) <= 3 and len(p[0]) > 1 and p[0].isalpha() else p
    x, y = expand(x), expand(y)
    if len(x) != len(y) or not x:
        return False
    for a, b in zip(x, y):
        if a == b:
            continue
        if (len(a) == 1 and b.startswith(a)) or (len(b) == 1 and a.startswith(b)):
            continue
        return False
    return True
