import re
import sqlite3
import threading
import unicodedata

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, norm TEXT NOT NULL, added_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_series_norm ON series(norm);
CREATE TABLE IF NOT EXISTS edition (
    id INTEGER PRIMARY KEY, series_id INTEGER NOT NULL REFERENCES series(id),
    lang TEXT NOT NULL, name TEXT NOT NULL, norm TEXT NOT NULL,
    audible_asin TEXT, monitored INTEGER, link_source TEXT NOT NULL DEFAULT 'abs',
    synced_at TEXT, sync_error TEXT,
    UNIQUE(lang, norm)
);
CREATE TABLE IF NOT EXISTS book (
    id INTEGER PRIMARY KEY, edition_id INTEGER REFERENCES edition(id),
    lang TEXT NOT NULL, asin TEXT, title TEXT NOT NULL, subtitle TEXT,
    author TEXT, narrator TEXT, sequence TEXT, seq REAL,
    release_date TEXT, runtime_min INTEGER, cover_url TEXT,
    abs_item_id TEXT UNIQUE, owned INTEGER NOT NULL DEFAULT 0,
    size INTEGER, num_files INTEGER, added_at TEXT,
    progress REAL, finished INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_book_edition ON book(edition_id);
CREATE INDEX IF NOT EXISTS idx_book_asin ON book(asin);
CREATE TABLE IF NOT EXISTS author_catalog (
    author_norm TEXT NOT NULL, lang TEXT NOT NULL, series_asin TEXT NOT NULL,
    series_title TEXT NOT NULL, book_count INTEGER NOT NULL, cover_url TEXT,
    first_asin TEXT, fetched_at TEXT NOT NULL,
    PRIMARY KEY(author_norm, lang, series_asin)
);
CREATE TABLE IF NOT EXISTS author_fetch (
    author_norm TEXT NOT NULL, lang TEXT NOT NULL, fetched_at TEXT NOT NULL,
    PRIMARY KEY(author_norm, lang)
);
CREATE TABLE IF NOT EXISTS watchlist (
    asin TEXT PRIMARY KEY, lang TEXT, title TEXT, author TEXT, series TEXT,
    cover_url TEXT, kind TEXT NOT NULL DEFAULT 'book', added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_run (
    id INTEGER PRIMARY KEY, key TEXT NOT NULL, started TEXT NOT NULL,
    finished TEXT, status TEXT NOT NULL, message TEXT
);
CREATE TABLE IF NOT EXISTS download (
    id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, source TEXT NOT NULL, indexer TEXT,
    release_title TEXT NOT NULL, guid TEXT, size INTEGER, score INTEGER, reasons TEXT,
    marker TEXT NOT NULL UNIQUE, client_id TEXT, state TEXT NOT NULL, progress REAL,
    path TEXT, error TEXT, auto INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_download_book ON download(book_id);
CREATE TABLE IF NOT EXISTS setting (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS abs_tag (item TEXT NOT NULL, tag TEXT NOT NULL, PRIMARY KEY(item, tag));
CREATE TABLE IF NOT EXISTS book_search (
    book_id INTEGER PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0, last_search TEXT, next_search TEXT,
    last_result TEXT, hits INTEGER, unsure INTEGER
);
CREATE TABLE IF NOT EXISTS book_result (book_id INTEGER PRIMARY KEY, searched_at TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS book_reject (book_id INTEGER NOT NULL, guid TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(book_id, guid));
CREATE TABLE IF NOT EXISTS book_hide (book_id INTEGER NOT NULL, guid TEXT NOT NULL, until TEXT NOT NULL, PRIMARY KEY(book_id, guid));
CREATE TABLE IF NOT EXISTS convert_job (
    id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, state TEXT NOT NULL, message TEXT,
    started TEXT NOT NULL, finished TEXT, src_files INTEGER, src_bytes INTEGER, out_bytes INTEGER
);
CREATE TABLE IF NOT EXISTS chapter_job (book_id INTEGER PRIMARY KEY, state TEXT NOT NULL, message TEXT, at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS libsplit_job (
    book_id INTEGER PRIMARY KEY, title TEXT, rel_path TEXT, lang TEXT, state TEXT NOT NULL, message TEXT,
    parent_id INTEGER, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ebook_job (
    id INTEGER PRIMARY KEY, book_id INTEGER NOT NULL, indexer TEXT, release_title TEXT NOT NULL, guid TEXT, size INTEGER,
    score INTEGER, marker TEXT NOT NULL UNIQUE, client_id TEXT, state TEXT NOT NULL, error TEXT, imported_to TEXT,
    auto INTEGER NOT NULL DEFAULT 0, created TEXT NOT NULL, updated TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ebook_job_book ON ebook_job(book_id);
CREATE TABLE IF NOT EXISTS ebook_search (
    book_id INTEGER PRIMARY KEY, attempts INTEGER NOT NULL DEFAULT 0, last_search TEXT, next_search TEXT,
    last_result TEXT, message TEXT
);
CREATE TABLE IF NOT EXISTS session (token_hash TEXT PRIMARY KEY, created REAL NOT NULL, expires REAL NOT NULL, agent TEXT);
CREATE TABLE IF NOT EXISTS wishlist_item (
    source TEXT NOT NULL, ext_id TEXT NOT NULL, title TEXT NOT NULL, author TEXT, asin TEXT, lang TEXT,
    state TEXT NOT NULL, checked_at TEXT NOT NULL, PRIMARY KEY(source, ext_id)
);
CREATE TABLE IF NOT EXISTS user (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, hash TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'user',
    goodreads_user TEXT NOT NULL DEFAULT '', goodreads_shelf TEXT NOT NULL DEFAULT 'to-read', created TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS follow (
    user_id INTEGER NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(user_id, kind, key)
);
CREATE TABLE IF NOT EXISTS notification (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT, link TEXT, cover TEXT,
    dedupe TEXT NOT NULL, created TEXT NOT NULL, read_at TEXT, UNIQUE(user_id, dedupe)
);
CREATE INDEX IF NOT EXISTS idx_notification_user ON notification(user_id, id);
CREATE TABLE IF NOT EXISTS user_progress (
    user_id INTEGER NOT NULL, book_id INTEGER NOT NULL, progress REAL, finished INTEGER NOT NULL DEFAULT 0, updated TEXT,
    PRIMARY KEY(user_id, book_id)
);
-- Zusammengeführte Karte: Serienname aus Audiobookshelf -> Fassung, auf die er zeigt (abw/merge.py)
CREATE TABLE IF NOT EXISTS edition_alias (
    lang TEXT NOT NULL, norm TEXT NOT NULL, edition_id INTEGER NOT NULL, created TEXT NOT NULL, PRIMARY KEY(lang, norm)
);
-- Hörposition vor «als gehört markieren», damit «nicht gehört» sie zurückbringt
CREATE TABLE IF NOT EXISTS mark_undo (
    user_id INTEGER NOT NULL, book_id INTEGER NOT NULL, cur_time REAL, progress REAL, created TEXT NOT NULL, PRIMARY KEY(user_id, book_id)
);
CREATE TABLE IF NOT EXISTS rating (
    user_id INTEGER NOT NULL, book_id INTEGER NOT NULL, stars INTEGER, updated TEXT NOT NULL, PRIMARY KEY(user_id, book_id)
);
-- Buch gelöscht: Bewertung mit, sonst hinge sie an einem neuen Buch, das dieselbe ID bekommt
CREATE TRIGGER IF NOT EXISTS rating_book_gone AFTER DELETE ON book BEGIN DELETE FROM rating WHERE book_id=OLD.id; END;
CREATE TABLE IF NOT EXISTS auto_follow (
    user_id INTEGER NOT NULL, kind TEXT NOT NULL, key TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(user_id, kind, key)
);
CREATE TABLE IF NOT EXISTS push_sub (
    endpoint TEXT PRIMARY KEY, user_id INTEGER NOT NULL, p256dh TEXT NOT NULL, auth TEXT NOT NULL, agent TEXT,
    created TEXT NOT NULL, last_ok TEXT, fails INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS listen_session (
    id TEXT PRIMARY KEY, abs_user_id TEXT NOT NULL, username TEXT, item_id TEXT, lang TEXT, title TEXT, author TEXT,
    day TEXT NOT NULL, started TEXT NOT NULL, updated TEXT NOT NULL, seconds INTEGER NOT NULL DEFAULT 0, raw_seconds INTEGER,
    pos_start REAL, pos_end REAL, duration REAL, device TEXT
);
CREATE INDEX IF NOT EXISTS idx_listen_day ON listen_session(day, abs_user_id);
CREATE TABLE IF NOT EXISTS listen_progress (
    abs_user_id TEXT NOT NULL, item_id TEXT NOT NULL, progress REAL, finished INTEGER NOT NULL DEFAULT 0, finished_at TEXT, updated TEXT,
    PRIMARY KEY(abs_user_id, item_id)
);
CREATE TABLE IF NOT EXISTS author_auto (
    lang TEXT NOT NULL, series_asin TEXT NOT NULL, author_norm TEXT NOT NULL, created TEXT NOT NULL,
    PRIMARY KEY(lang, series_asin)
);
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY, value TEXT NOT NULL, fetched_at TEXT NOT NULL
);
-- «To Read» je Benutzer (abw/toread.py): kind edition = ganze Fassung (zeigt den nächsten Band), book = Einzelbuch
CREATE TABLE IF NOT EXISTS toread (
    user_id INTEGER NOT NULL, kind TEXT NOT NULL, ref_id INTEGER NOT NULL, pos REAL NOT NULL, source TEXT NOT NULL,
    added TEXT NOT NULL, PRIMARY KEY(user_id, kind, ref_id)
);
-- Aus Entdecken auf To Read gesetzt, aber noch nicht in der Bibliothek (Audible war nicht erreichbar): nach der Aufnahme einreihen
CREATE TABLE IF NOT EXISTS toread_pending (user_id INTEGER NOT NULL, asin TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(user_id, asin));
-- Weiterempfehlen: Serie (kind edition) oder Einzelbuch von einem Benutzer an einen anderen; state open, accepted, declined
CREATE TABLE IF NOT EXISTS toread_rec (
    id INTEGER PRIMARY KEY, from_user INTEGER NOT NULL, to_user INTEGER NOT NULL, kind TEXT NOT NULL, ref_id INTEGER NOT NULL,
    created TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'open', UNIQUE(from_user, to_user, kind, ref_id)
);
-- Vorschlag «nicht auf die Liste»: ausgeblendet, solange book_id der nächste Band ist
CREATE TABLE IF NOT EXISTS toread_dismiss (user_id INTEGER NOT NULL, edition_id INTEGER NOT NULL, book_id INTEGER, created TEXT NOT NULL, PRIMARY KEY(user_id, edition_id));
-- Drei Serien aus Entdecken unter der Liste: «Nicht auf die Liste» (Buch- und Serien-ASIN)
CREATE TABLE IF NOT EXISTS toread_pick_dismiss (user_id INTEGER NOT NULL, asin TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(user_id, asin));
-- Von Hand entfernte Serien: nicht automatisch wieder einreihen
CREATE TABLE IF NOT EXISTS toread_skip (user_id INTEGER NOT NULL, edition_id INTEGER NOT NULL, created TEXT NOT NULL, PRIMARY KEY(user_id, edition_id));
CREATE TABLE IF NOT EXISTS integrity (  -- Bestandsprüfung: Lücken aus Null-Bytes (unfertige Torrents)
    book_id INTEGER PRIMARY KEY, checked_at TEXT NOT NULL, sig TEXT NOT NULL, state TEXT NOT NULL, detail TEXT
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.c = c
    return c


MIGRATIONS = [  # (Tabelle, Spalte, Typ) für Spalten, die nach der ersten Version dazukamen
    ("book", "rel_path", "TEXT"),
    ("book", "audible_min", "INTEGER"),  # Laufzeit laut Audible; runtime_min ist bei Büchern im Bestand die Dauer aus Audiobookshelf
    ("user", "follow_on_add", "INTEGER NOT NULL DEFAULT 1"),  # beim Hinzufügen im Entdecken der Serie folgen
    ("edition", "drama", "INTEGER"),  # Serie ist bei Audible überwiegend Hörspiel (Die drei ???, TKKG)
    ("download", "checks", "TEXT"),
    ("download", "verified_at", "TEXT"),
    ("download", "imported_to", "TEXT"),
    ("book", "num_chapters", "INTEGER"),
    ("download", "bundle", "TEXT"),  # Sammelband: "[14, 16]" = enthält die Bände 14 bis 16
    ("download", "parent_id", "INTEGER"),
    ("download", "misfit", "INTEGER"),  # 1 = aus Sammelband nach Titel herausgelöst, Laufzeit passt nicht: nur von Hand ablegen
    ("download", "replaces", "INTEGER"),  # 1 = Band liegt schon im Bestand; nur nach Vergleich und von Hand ersetzen  # Teil eines aufgeteilten Sammelbands
    ("series", "auto", "INTEGER"),
    ("book", "epub", "INTEGER"),
    ("ebook_job", "source", "TEXT"),  # usenet oder torrent (EBookBay, Internet Archive)
    ("ebook_job", "manual", "INTEGER"),  # 1 = Treffer von Hand gewählt
    ("session", "user_id", "INTEGER"),
    ("book", "wanted", "TEXT"),  # Zeitpunkt, zu dem das Buch von Hand hinzugefügt wurde: wird gesucht (mit Vorrang), auch ohne überwachte Serie
    ("book", "wanted_by", "INTEGER"),
    ("book", "pre_path", "TEXT"),  # Ordner eines noch fehlenden Bandes, in dem schon das E-Book liegt (relativ zur Bibliothek)
    ("series", "prio", "TEXT"),  # Zeitpunkt des Hinzufügens: fehlende Bände dieser Serie sucht die Automatik zuerst
    ("watchlist", "user_id", "INTEGER"),
    ("watchlist", "state", "TEXT"),  # NULL/pending = noch nicht in die Bibliothek aufgenommen, done, error
    ("watchlist", "message", "TEXT"),
    ("watchlist", "series_id", "INTEGER"),
    ("watchlist", "book_id", "INTEGER"),
    ("user", "abs_user_id", "TEXT"),  # verknüpftes Konto in Audiobookshelf: Anmeldung mit dessen Passwort, eigener Hörfortschritt
    ("user", "abs_name", "TEXT"),  # Benutzername in Audiobookshelf (wird beim Abgleich nachgeführt)
    ("user", "abs_active", "INTEGER"),
    ("user", "abs_session", "TEXT"),  # verschlüsselter Erneuerungsschlüssel der Audiobookshelf-Sitzung (nur zum Setzen des Hörstands)
    ("user", "abs_session_at", "TEXT"),  # 0 = Konto in Audiobookshelf gesperrt oder gelöscht
    ("book", "abs_match", "TEXT"),  # von ABW abgelegt und in Audiobookshelf mit Audible gematcht: Zeitpunkt, «Fehler: …» oder «offen: {Serien, ASIN}» während des Matches
    ("ebook_job", "seq_ok", "INTEGER"),  # 1 = der Treffer nannte die richtige Bandnummer  # 1 = im Buchordner liegt ein EPUB  # NULL/1 = Automatik folgt dem Hauptschalter, 0 = für diese Serie aus
    ("edition", "kind", "TEXT"),
    ("edition", "variant", "TEXT"),  # zusätzliche Fassung derselben Sprache («Rob Inglis», «BBC-Hörspiel»); NULL = Hauptfassung
    ("edition", "narr", "TEXT"),  # Sprecher, an den die Fassung gebunden ist
    ("edition", "base_id", "INTEGER"),  # Sprecher-Fassung: Hauptfassung, aus deren Audiobookshelf-Serie die Bücher kommen
    ("edition", "vlock", "INTEGER"),  # 1 = Fassung von Hand getrennt, nicht wieder automatisch zuordnen  # ohne Audible-Serie: single = Einzelbücher, absent = nicht bei Audible, NULL = ungeprüft
]


# Dasselbe Hörbuch steht mehrfach in der Tabelle: gleiche ASIN (unter zwei Serien) oder ein doppelter Audible-Eintrag
# (andere ASIN, aber gleicher Titel, gleiche Sprache, Laufzeit bis 2 Minuten gleich, Sprecher nicht widersprüchlich).
# Nur für Bände, die nicht im Bestand liegen; Platzhalter wie ASIN «0» zählen nicht.
TWIN_VIEW = """
CREATE INDEX IF NOT EXISTS idx_book_title ON book(title COLLATE NOCASE);
DROP VIEW IF EXISTS book_twin;
CREATE VIEW book_twin AS
SELECT b.id AS id, o.id AS twin_id FROM book b JOIN book o ON b.owned=0 AND o.id<>b.id AND (
    (length(b.asin)=10 AND o.asin=b.asin)
 OR (o.title=b.title COLLATE NOCASE AND o.lang=b.lang AND b.runtime_min>0
     AND o.runtime_min BETWEEN b.runtime_min-2 AND b.runtime_min+2
     AND (COALESCE(b.narrator,'')='' OR COALESCE(o.narrator,'')='' OR o.narrator=b.narrator COLLATE NOCASE)));
"""
# Bände, die als fehlend geführt sind, obwohl dasselbe Hörbuch schon im Bestand liegt
HAVE_TWIN = "SELECT t.id FROM book_twin t JOIN book o ON o.id=t.twin_id WHERE o.owned=1"
# Bände, deren Doppel gerade lädt oder auf Prüfung und Ablage wartet
BUSY_TWIN = ("SELECT t.id FROM book_twin t JOIN download d ON d.book_id=t.twin_id"
             " WHERE d.state IN ('queued','downloading','arrived','verified','review','imported')")
# Dasselbe als Bedingung für eine Abfrage mit «book b» (schneller als die ganze Liste)
B_HAS_TWIN = "EXISTS (SELECT 1 FROM book_twin t JOIN book o ON o.id=t.twin_id WHERE t.id=b.id AND o.owned=1)"
B_TWIN_BUSY = ("EXISTS (SELECT 1 FROM book_twin t JOIN download d ON d.book_id=t.twin_id WHERE t.id=b.id"
               " AND d.state IN ('queued','downloading','arrived','verified','review','imported'))")


def init() -> None:
    c = conn()
    c.executescript(SCHEMA)
    c.executescript(TWIN_VIEW)
    for table, col, typ in MIGRATIONS:
        if col not in {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    migrate_users(c)


def migrate_users(c) -> None:
    """Bis Etappe 4 gab es einen einzigen Benutzer in den Einstellungen. Er wird der erste Administrator (Nummer 1)
    und behält Passwort, Sitzungen, Goodreads-Regal und Wunschliste."""
    get = lambda k: (c.execute("SELECT value FROM setting WHERE key=?", (k,)).fetchone() or {"value": ""})["value"]
    if get("auth_hash") and not c.execute("SELECT 1 FROM user").fetchone():
        c.execute("INSERT INTO user(id, name, hash, role, goodreads_user, goodreads_shelf, created) VALUES(1,?,?,'admin',?,?,datetime('now'))",
                  (get("auth_user") or "admin", get("auth_hash"), get("goodreads_user"), get("goodreads_shelf") or "to-read"))
        c.execute("DELETE FROM setting WHERE key IN ('auth_hash','auth_user')")
    c.execute("UPDATE session SET user_id=1 WHERE user_id IS NULL")
    c.execute("UPDATE watchlist SET user_id=1 WHERE user_id IS NULL")
    c.execute("UPDATE wishlist_item SET source='gr1' WHERE source='goodreads'")


DEFAULTS = {"auto_search": "0", "auto_import": "0", "auto_batch": "10", "auto_cleanup": "0", "auto_convert": "0", "auto_chapters": "0", "notify": "0", "auto_ebook": "0", "auto_ebook_upcoming": "0", "auto_ebook_listening": "1", "notify_ebook": "0", "auto_author_series": "0", "auto_integrity": "0",
            "setup_done": "0", "auth_user": "", "auth_hash": "", "goodreads_user": "", "goodreads_shelf": "to-read"}


def setting(key: str) -> str:
    r = conn().execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
    return r["value"] if r else DEFAULTS[key]


def set_setting(key: str, value: str) -> None:
    if key not in DEFAULTS:
        raise KeyError(key)
    conn().execute("INSERT OR REPLACE INTO setting VALUES(?,?)", (key, value))


def norm(text: str | None) -> str:
    """Vergleichsschlüssel: Kleinbuchstaben, ohne Akzente, Artikel und Sonderzeichen."""
    t = unicodedata.normalize("NFKD", (text or "").lower().replace("ß", "ss"))
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"\((german|deutsche?|english|englische?) (edition|ausgabe)\)", " ", t)
    t = re.sub(r"\b(the|a|an|der|die|das|series|saga|serie|reihe|trilogy|trilogie)\b", " ", t)
    return re.sub(r"[^a-z0-9]+", "", t)


def parse_seq(sequence: str | None) -> float | None:
    if not sequence:
        return None
    m = re.match(r"^\s*(\d+(?:[.,]\d+)?)\s*$", sequence)
    return float(m.group(1).replace(",", ".")) if m else None
