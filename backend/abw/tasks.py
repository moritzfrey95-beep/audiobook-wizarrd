"""Einmalige und zyklische Aufgaben. Ein Hintergrund-Thread, eine Aufgabe gleichzeitig."""
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import abs as abs_mod
from . import audible, chapters, cleanup, convert, db, ebook, goodreads, integrity, libsplit, library, librarytags, listened, listening, notifications, search, wishlist

# key -> (Name, Beschreibung, Intervall in Minuten, Funktion)
TASKS = {
    "abs": ("Bestand abgleichen", "Liest Bücher und Serien aus Audiobookshelf.", 30, abs_mod.sync),
    "abs_scan": ("Bibliothek komplett einlesen", "Rückfallebene: lässt Audiobookshelf beide Bibliotheken ganz neu einlesen. Neue Bücher meldet ABW sonst einzeln über den Ordner-Wächter.", 1440, abs_mod.scan_all),
    "abs_match": ("In Audiobookshelf matchen", "Gleicht von ABW abgelegte Bücher in Audiobookshelf über die ASIN mit Audible ab und übernimmt alle Angaben samt Cover von Audible, ausser Serie und ASIN: die bleiben, wie sie vorher waren. Höchstens 40 Bücher pro Lauf.", 5, abs_mod.match_imported),
    "abs_tags": ("«Gewünscht von» taggen", "Setzt in Audiobookshelf den Tag «Gewünscht von <Name>» bei allen vorhandenen Büchern einer Serie, die ein Benutzer gewünscht hat (Buch, Serie oder anderer Band). Administratoren und Konten ohne Audiobookshelf nicht. Jeder Tag nur einmal: von Hand entfernt, bleibt er weg. Höchstens 100 Einträge pro Lauf.", 60, abs_mod.tag_wishes),
    "listening": ("Hörfortschritt abgleichen", "Liest den Hörfortschritt jedes mit Audiobookshelf verknüpften Benutzers. Hat jemand ein Buch fertig gehört, werden Serie und Autor für ihn markiert.", 10, abs_mod.sync_progress),
    "sessions": ("Hörverlauf abgleichen", "Liest neue Hörsitzungen aller Konten aus Audiobookshelf (wer wann was gehört hat) für den Hörverlauf. Nur lesend.", 15, listening.sync),
    "abs_sessions": ("Audiobookshelf-Sitzungen erneuern", "Erneuert die Sitzungen, mit denen ABW für andere Benutzer «als gehört markieren» nach Audiobookshelf überträgt. Ohne Erneuerung verfallen sie nach 30 Tagen.", 1440, listened.keep_alive),
    "convert": ("MP3 zu M4B umwandeln", "Fasst ein Buch aus vielen Dateien zu einer M4B mit Kapiteln zusammen, prüft sie und legt die Originale in den Papierkorb. Ein Buch pro Lauf, nur bei eingeschaltetem Schalter.", 10, convert.auto),
    "chapters": ("Kapitel ergänzen", "Setzt bei Büchern aus einer Datei ohne Kapitel die Kapitel von Audible, wenn die Laufzeit passt. Nur bei eingeschaltetem Schalter.", 60, chapters.auto),
    "libsplit": ("Sammelbände im Bestand aufteilen", "Schneidet Sammelbände aus einer Datei (Publisher's Pack, «Books 1-3») an den Kapitelgrenzen in Einzelbände, wenn Kapiteltitel oder Audible-Laufzeiten die Grenzen sicher zeigen. Die Bände laufen durch Prüfung und Ablage; sind alle abgelegt, geht der Sammelband in den Papierkorb. Höchstens drei pro Lauf.", 60, libsplit.auto),
    "integrity": ("Bestand auf Schäden prüfen", "Liest nachts (01–07 Uhr) jede Hörbuchdatei im Bestand einmal ganz und sucht Lücken aus Null-Bytes, wie sie unfertige Torrents hinterlassen. Beschädigte Bücher werden neu gesucht; die neue Fassung ersetzt sie nach der Ankunftsprüfung. Unveränderte Bücher werden nicht noch einmal gelesen. Nur bei eingeschaltetem Schalter.", 10, integrity.auto),
    "downloads": ("Downloads verfolgen", "Fragt den Stand der von ABW gestarteten Downloads in SABnzbd, qBittorrent und den Download-Clients der Erweiterungen ab; fertige Torrents werden sofort gestoppt.", 1, search.track),
    "auto": ("Suchlauf (Automatik)", "Sucht fällige fehlende Bände überwachter Serien und lädt sichere Treffer. Läuft nur bei eingeschalteter Automatik.", 15, search.auto_run),
    "verify": ("Ankunftsprüfung", "Prüft angekommene Hörbücher: Dateien, Laufzeit gegen Audible, Tags und gesprochene Ansage.", 1, search.verify_arrived),
    "cleanup": ("Aufräumen", "Verschiebt Reste abgelegter, defekter und verworfener Downloads nach einem Tag in den Papierkorb. Läuft nur bei eingeschaltetem Schalter.", 60, cleanup.auto),
    "ebooks": ("E-Books im Bestand prüfen", "Merkt je Buch, ob in seinem Ordner ein EPUB liegt. Liest nur, ändert keine Dateien.", 360, ebook.scan),
    "ebook_dl": ("E-Book-Downloads verfolgen", "Fragt den Stand der EPUB-Downloads in SABnzbd und qBittorrent ab, prüft angekommene EPUBs (Titel, Autor, Sprache) und legt sie in den Ordner des Hörbuchs.", 1, ebook.track),
    "ebook_auto": ("E-Books suchen (Automatik)", "Sucht das EPUB zu neu abgelegten Hörbüchern und vorab zu angekündigten Bänden, deren Hörbuch noch nicht erschienen ist (Usenet, sonst EBookBay und Internet Archive), je drei Bücher pro Lauf. Jeder Teil hat seinen Schalter.", 30, ebook.auto),
    "added": ("Hinzugefügtes aufnehmen", "Nimmt Serien und Bücher, die unter Entdecken oder über Goodreads hinzugefügt wurden, als fehlend in die Bibliothek auf, falls das beim Hinzufügen nicht gelang.", 10, library.pending),
    "author_series": ("Serien gehörter Autoren aufnehmen", "Hat irgendein Benutzer ein Buch fertig gehört, kommen alle Serien dieses Autors aus dem Autorenkatalog auf Deutsch und Englisch als fehlend in die Bibliothek; Sprachfassungen derselben Serie kommen auf eine Karte, wo das eindeutig ist. Gekürzte Fassungen nicht. Jede Serie nur einmal: Wird sie wieder entfernt, bleibt sie weg. Nur bei eingeschaltetem Schalter.", 60, library.finished_authors),
    "notify": ("Benachrichtigungen", "Meldet erschienene Hörbücher markierter Serien und Autoren und räumt Meldungen weg, die älter als ein halbes Jahr sind.", 60, notifications.released),
    "series": ("Serien bei Audible prüfen", "Holt alle Bände und Erscheinungstermine je Serie; findet fehlende und angekündigte Bände.", 360, audible.sync_series),
    "wishlist": ("Wunschliste abgleichen", "Liest die 100 neuesten Bücher der verknüpften Goodreads-Regale (und eingelesene Exporte), sucht sie bei Audible und nimmt Treffer als fehlend in die Bibliothek auf; die Suche läuft dann mit Vorrang.", 720, wishlist.sync),
    "goodreads": ("Goodreads-Genres holen", "Holt über FlareSolverr die Leser-Genres von Goodreads (LitRPG, Cozy, Epic Fantasy …) für gehörte Bücher und die besten Vorschläge unter «Für dich», höchstens 40 Bücher pro Lauf. Ergänzt die Tags aus Audible; einen Monat zwischengespeichert.", 60, goodreads.sync),
    "library_tags": ("Genres und Tags der Bibliothek", "Holt Kategorien und Beschreibung der ersten Bände jeder Serie und aller Einzelbücher bei Audible, damit sich die Serienansicht nach Genre und Tag filtern lässt. Höchstens 1000 Bücher pro Lauf, Audible-Angaben einen Monat zwischengespeichert; neue Serien erscheinen im Filter nach dem nächsten Lauf.", 60, librarytags.sync),
    "authors": ("Autorenkatalog prüfen", "Sucht weitere Serien deiner Autoren auf Audible.com und Audible.de und verknüpft Sprachfassungen.", 1440, audible.sync_authors),
}
PLANNED = [
    ("upgrade", "Qualitäts-Upgrade", "Lädt eine bessere Fassung nach, wenn ein Buch nur in schwacher Qualität vorliegt.", "Etappe 4"),
    ("manual", "Handimport", "Nimmt von Hand abgelegte Hörbücher aus einem Eingangsordner auf.", "Etappe 4"),
]

_state = {k: {"running": False, "progress": "", "queued": False} for k in TASKS}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _last(key: str):
    return db.conn().execute("SELECT * FROM task_run WHERE key=? ORDER BY id DESC LIMIT 1", (key,)).fetchone()


def run(key: str) -> None:
    name, _, _, fn = TASKS[key]
    c = db.conn()
    st = _state[key]
    st.update(running=True, queued=False, progress="gestartet")
    rid = c.execute("INSERT INTO task_run(key, started, status) VALUES(?,?,?)", (key, _now().isoformat(), "running")).lastrowid
    try:
        msg = fn(lambda m: st.update(progress=m))
        status = "ok"
    except Exception as exc:
        traceback.print_exc()
        if c.in_transaction:
            c.execute("ROLLBACK")
        msg, status = str(exc)[:500], "error"
    c.execute("UPDATE task_run SET finished=?, status=?, message=? WHERE id=?", (_now().isoformat(), status, msg, rid))
    c.execute("DELETE FROM task_run WHERE key=? AND id NOT IN (SELECT id FROM task_run WHERE key=? ORDER BY id DESC LIMIT 50)", (key, key))
    st.update(running=False, progress="")


def request(key: str) -> None:
    _state[key]["queued"] = True


def _due(key: str) -> bool:
    if _state[key]["queued"]:
        return True
    last = _last(key)
    if not last:
        return True
    # Nach einem Fehler oder Abbruch schon nach fünf Minuten erneut versuchen
    wait = 5 if last["status"] == "error" else TASKS[key][2]
    return _now() - datetime.fromisoformat(last["started"]) >= timedelta(minutes=wait)


SLOW = ("series", "authors")  # lange Läufe blockieren die kurzen nicht


def loop(keys) -> None:
    while True:
        for key in keys:
            try:
                if db.setting("setup_done") != "1":
                    break  # vor dem Setup-Wizard fehlt Audiobookshelf: nichts planmässig starten
                if _due(key):
                    run(key)
            except Exception:
                traceback.print_exc()
        time.sleep(5)


def start() -> None:
    db.conn().execute("UPDATE task_run SET status='error', message='Durch Neustart abgebrochen', finished=started WHERE status='running'")
    # Jede Aufgabe läuft für sich; nur die beiden Audible-Aufgaben teilen sich einen Faden (Abfragetempo)
    for group in [(k,) for k in TASKS if k not in SLOW] + [SLOW]:
        threading.Thread(target=loop, args=(group,), daemon=True, name="abw-" + group[0]).start()


def overview() -> dict:
    c = db.conn()
    out = []
    for key, (name, desc, interval, _) in TASKS.items():
        last = _last(key)
        wait = 5 if last and last["status"] == "error" else interval
        nxt = (datetime.fromisoformat(last["started"]) + timedelta(minutes=wait)).isoformat() if last else None
        out.append({"key": key, "name": name, "description": desc, "interval_min": interval, "kind": "cyclic",
                    **_state[key], "last": dict(last) if last else None, "next": nxt})
    runs, seen = [], set()
    for r in c.execute("SELECT * FROM task_run ORDER BY id DESC LIMIT 400"):
        # Häufige Routineläufe nur einmal zeigen, Fehler immer
        if r["status"] == "ok" and r["key"] in ("abs", "listening", "sessions", "ebooks", "ebook_dl", "ebook_auto", "downloads", "verify", "auto", "cleanup", "convert", "chapters", "libsplit", "integrity", "wishlist", "added", "author_series", "notify", "goodreads") and r["key"] in seen:
            continue
        seen.add(r["key"])
        runs.append(dict(r))
        if len(runs) >= 40:
            break
    for r in runs:
        r["name"] = TASKS.get(r["key"], (r["key"],))[0]
    planned = [{"key": k, "name": n, "description": d, "stage": s} for k, n, d, s in PLANNED]
    return {"tasks": out, "runs": runs, "planned": planned}
