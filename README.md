<div align="center">

# 🪄 AudioBook Wizarrd

**Your audiobook series, complete.**
A self-hosted companion for [Audiobookshelf](https://www.audiobookshelf.org/) – think *Sonarr, but for audiobook series*.

[![CI](https://github.com/moritzfrey95-beep/audiobook-wizarrd/actions/workflows/ci.yml/badge.svg)](https://github.com/moritzfrey95-beep/audiobook-wizarrd/actions/workflows/ci.yml)
[![License: GPL-3.0](https://img.shields.io/badge/license-GPL--3.0-blue.svg)](LICENSE)
![Version](https://img.shields.io/badge/version-0.6.1-8b74ff)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker&logoColor=white)
![Languages](https://img.shields.io/badge/UI-English%20%7C%20Deutsch-555)

[Features](#-features) · [Screenshots](#-screenshots) · [Quick start](#-quick-start) · [Configuration](#%EF%B8%8F-configuration) · [How it works](#-how-it-works) · [FAQ](#-faq) · [Deutsch](#-deutsch)

<img src="docs/screenshots/series.png" alt="Series overview" width="900">

</div>

---

## 👋 What is this?

You love long audiobook series. Audiobookshelf plays them beautifully – but it does not tell you that volume 7 came out last
week, that the German edition is missing two books, or that a new volume is announced for March.

**AudioBook Wizarrd (ABW)** looks at every series in your library, compares it with everything Audible knows about that series,
and shows you at a glance what is complete, what is missing and what is coming. If you want, it goes one step further: it searches
your own indexers through Prowlarr, downloads with SABnzbd or qBittorrent, **checks every download** (runtime, tags, a spoken
intro) and files it neatly into your library – then tells Audiobookshelf to rescan.

Everything runs on your own server. ABW talks only to services you run yourself and to the public Audible catalogue.

> **Status:** early (0.x). ABW is used every day on one home server with a library of several thousand audiobooks.
> Expect rough edges, and please [open an issue](../../issues) when you find one.

---

## ✨ Features

ABW is organised like your day with audiobooks: **Listen**, **Library**, **Discover**, **New**, **Recap** and – for admins – the **Workshop**.

### 🎧 Listen – what's next, for everyone in the house
- **To Read list** per user, one line per series: the next volume, progress across the whole series (volumes and hours) and an
  **estimated finish date** based on your own listening pace
- **Fills itself:** finish a volume and the series moves up with the next one; reorder by hand, put things at the top or bottom
- **Recommend to others:** pick a series you've finished and send it to another user – it shows up in their To Read, where they
  decide *top*, *bottom* or *not on my list*. ABW won't let you recommend what the other person already heard or has listed
- **Admins decide who may recommend to whom:** only to admins, everyone to everyone, a custom *from → to* table, or off
- **Rate what you heard** with 1–5 stars (whole series at once or per volume) – ABW reminds you after you finish a book
- **Mark as listened** in ABW, synced back to Audiobookshelf for every linked account
- Three hand-picked **suggestions from Discover** below the list: three genres, one of them "something different"

### 📚 Library – every series, complete
- **Every series with all its volumes** from the public Audible catalogue (audible.com and audible.de): covers, release dates, runtimes, narrators
- **Status at a glance:** complete, incomplete, new book coming, actively searching, downloading, in review
- **English and German editions on one card**, plus alternative recordings (full cast, dramatized, other narrators) as their own rows
- **Standalone books** grouped per author, **omnibus editions** recognised and not counted as missing volumes
- **Filters** by language, status, genre, tag and who requested it; **sort** by name, recently added, missing volumes, next release, length
- **Series page:** every volume with your rating, listened state, EPUB, a manual or automatic search button and per-series automation
- **Authors:** all authors with books in German and English, and how many more series Audible has from them

### 🧭 Discover – find your next series
- **For you:** suggestions built from what you finished and how you rated it – more volumes heard and more stars count more,
  low ratings push similar titles down. Sub-genres like LitRPG, Cozy or Isekai come from Audible and (optionally) Goodreads
- **From your authors:** series by authors you already listen to that are not in your library yet
- **Top lists:** Audible bestsellers and new releases per language, and a **free search** of the Audible catalogue
- One click on **Add** puts a title or series into the library as *missing* (searched with priority); **Add to To Read** also queues it for you
- **Goodreads:** link your shelf (or import the CSV export) – new books on it are added and searched automatically

### 🔔 New – never miss a release
- **Follow** series and authors with the bell: get notified when a new volume is announced, released, or lands in your library,
  and when an author starts a new series
- **Notifications** in the app, as **browser push** on your phone, or via [ntfy](https://ntfy.sh)
- **Release calendar** of upcoming volumes for the series you follow (or all languages)

### 📊 Recap – your listening, in numbers
- **Listening history** from Audiobookshelf's sessions: week, month and year recaps per account or for everyone, compared with the
  previous period – listening time, books, days, streaks, most listened, longest session, devices and apps, a weekday × hour heat map,
  and books that were started but left lying
- **Statistics:** series, books, runtime, storage, missing and announced volumes, languages compared, longest series, authors and
  narrators by runtime, best authors / narrators / series by stars, star distribution, who requested what, and ABW's own activity
  in the last 24 hours and 7 days
- **My ratings** and a **top list per tag** (your best LitRPG, your best Cozy Fantasy …)

### 🔎 Workshop – search, download, check, import (admins)
- Search via **[Prowlarr](https://prowlarr.com/)** across your Usenet and torrent indexers, with several query variants per volume
- Download via **SABnzbd** (or anything with its API) and **qBittorrent**, each into its own folder; finished torrents are stopped
- **Scoring with reasons:** every result is rated *sure*, *possible* or *rejected* – and ABW tells you why (title, volume number,
  author, narrator, language, size, omnibus, uploaded before release …)
- **Guided search** for manual control, **automatic search** for the boring part (with back-off: 1, 2, 4, 7 days)
- **Arrival check** before anything touches your library: files, runtime vs. Audible (with a diagnosis: omnibus, other recording,
  incomplete), tags, and optionally **speech recognition** of the spoken intro (faster-whisper)
- **Omnibus downloads are split** into one folder per volume, by volume number or by Audible runtimes
- **Import** into the right folder (`Series/Book X - Title`), next to the volumes you already have; Audiobookshelf is told to rescan
  and the book is matched with Audible by ASIN
- **Better editions replace worse ones** automatically; old files go to a **trash folder**, nothing is deleted without a button press
- **Review** page for everything that needs a decision: import anyway, discard, check again, pick a result, link language editions,
  split omnibuses already in your library
- Optional **e-books** (EPUB) via Usenet, EBookBay or Internet Archive, checked (title, author, language) and placed next to the
  audiobook – even before the audiobook is released, if you like
- Library care: optional **MP3 → M4B** conversion with chapters, **chapters from Audible** for single-file books, **splitting
  omnibus files** already in your library at chapter borders, and a nightly **integrity scan** that finds broken files
  (e.g. zero-filled gaps from unfinished torrents) and searches for a replacement
- **Requested by** tags in Audiobookshelf show which user asked for a book
- **Tasks:** every background job with its schedule, last run and a *run now* button; every automation has its own switch

### 🧰 Built for self-hosters
- **One Docker image**, SQLite, no external database; slim by default, with an optional `-whisper` variant
- **Setup wizard** on first start, every connection editable and **testable in the UI**
- **Multiple users and roles:** create accounts, or **sign in with your Audiobookshelf account** and import its users
- **English and German** interface (per user, follows your browser), light and dark design, installable as an **app** (PWA)
- **Extensions:** add your own sources as plugins without forking ([details](#-extensions))
- Automation is **off by default** – nothing is downloaded or moved until you switch it on

---

## 📸 Screenshots

All screenshots use a demo library with made-up users.

### On the desktop

| To Read | Series detail |
|---|---|
| <img src="docs/screenshots/toread.png" alt="To Read" width="440"> | <img src="docs/screenshots/detail.png" alt="Series detail" width="440"> |
| **Recommend to other users** | **Who may recommend to whom (admin)** |
| <img src="docs/screenshots/recommend.png" alt="Recommend" width="440"> | <img src="docs/screenshots/rec-rules.png" alt="Recommendation rules" width="440"> |
| **Discover: for you** | **Discover: top lists** |
| <img src="docs/screenshots/foryou.png" alt="For you" width="440"> | <img src="docs/screenshots/toplists.png" alt="Top lists" width="440"> |
| **Rate what you heard** | **Top list per tag** |
| <img src="docs/screenshots/rate.png" alt="Rate" width="440"> | <img src="docs/screenshots/tagtop.png" alt="Top list per tag" width="440"> |
| **Authors** | **Notifications** |
| <img src="docs/screenshots/authors.png" alt="Authors" width="440"> | <img src="docs/screenshots/notifications.png" alt="Notifications" width="440"> |
| **Release calendar** | **Listening history** |
| <img src="docs/screenshots/calendar.png" alt="Calendar" width="440"> | <img src="docs/screenshots/listening.png" alt="Listening history" width="440"> |
| **Statistics** | **My ratings** |
| <img src="docs/screenshots/stats.png" alt="Statistics" width="440"> | <img src="docs/screenshots/myratings.png" alt="My ratings" width="440"> |
| **Review: what needs your decision** | **Tasks & automation** |
| <img src="docs/screenshots/review.png" alt="Review" width="440"> | <img src="docs/screenshots/tasks.png" alt="Tasks" width="440"> |
| **Setup wizard** | |
| <img src="docs/screenshots/wizard.png" alt="Setup wizard" width="440"> | |

### On the phone

| Series | Series detail | To Read | For you |
|---|---|---|---|
| <img src="docs/screenshots/m-series.png" alt="Series on the phone" width="200"> | <img src="docs/screenshots/m-detail.png" alt="Series detail on the phone" width="200"> | <img src="docs/screenshots/m-toread.png" alt="To Read on the phone" width="200"> | <img src="docs/screenshots/m-foryou.png" alt="For you on the phone" width="200"> |
| **Rate** | **Notifications** | **Listening history** | **Statistics** |
| <img src="docs/screenshots/m-rate.png" alt="Rate on the phone" width="200"> | <img src="docs/screenshots/m-notifications.png" alt="Notifications on the phone" width="200"> | <img src="docs/screenshots/m-listening.png" alt="Listening history on the phone" width="200"> | <img src="docs/screenshots/m-stats.png" alt="Statistics on the phone" width="200"> |

### Dark design

<p align="center"><img src="docs/screenshots/dark-detail.png" alt="Series detail in the dark design" width="880"></p>

---

## 🚀 Quick start

**You need:** Docker with Compose, a running Audiobookshelf with at least one *book* library, and an API key from
Audiobookshelf (*Settings → API Keys*). Prowlarr, SABnzbd and qBittorrent are optional.

```sh
git clone https://github.com/moritzfrey95-beep/audiobook-wizarrd.git
cd audiobook-wizarrd
cp .env.example .env
nano .env                      # at least MUSIC_ROOT_HOST and the folder names
docker compose up -d --build
```

Open **`http://<your-server>:8790`** and follow the setup wizard:

1. **Account** – create an admin (or skip: then ABW is only reachable from your home network)
2. **Audiobookshelf** – address, API key, pick your English and/or German library *(required)*
3. **Prowlarr** – test the connection, tick the indexers ABW may use
4. **SABnzbd / qBittorrent** – download clients and their finished-download folders
5. **Notifications** and **extras** – all optional

After the wizard ABW reads your library and asks Audible about every series. The first run takes a few minutes for a large
library. **All automation stays off** until you enable it under *Workshop → Tasks*.

> 💡 **Folders are always entered as ABW sees them inside the container.** Mount your library and download folders in
> `.env` / `compose.yaml` first; the wizard checks that each folder exists and is writable.

### Speech recognition (optional)

The intro check uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and makes the image considerably larger, so it is
off by default. Set `ABW_WHISPER=1` in `.env` and rebuild. Without it, the check simply skips the spoken intro.

---

## ⚙️ Configuration

There are two layers:

1. **`.env`** – everything Docker needs to know: folders to mount, user/group, ports, image variant.
2. **Settings → Connections** in the UI – URLs, API keys, libraries, indexers. Values saved here are stored in the database
   and **take precedence** over `.env`, so you can configure everything in the browser.

### Folders

| Variable | Mounted at | What it is |
|---|---|---|
| `ABW_DATA` | `/data` | Database, keys, caches. **Back this up.** |
| `MUSIC_ROOT_HOST` | `/library-root` | Folder containing your library folders (`LIBRARY_EN_DIR`, `LIBRARY_DE_DIR`) and the trash |
| `USENET_COMPLETE_HOST` | `/usenet-complete` | SABnzbd's finished folder for ABW's category |
| `TORRENT_COMPLETE_HOST` | `/torrent-complete` | qBittorrent's save folder for ABW (`QBIT_SAVE_ROOT` is the same folder as qBittorrent sees it) |
| `WHISPER_MODELS_HOST` | `/models/whisper` | Optional cache for the speech model |
| `ABW_PLUGINS_HOST` | `/plugins` | Optional extensions |

ABW runs as `ABW_UID:ABW_GID` (default `1000:1000`) – make sure that user may read the download folders and write to the library.

### Services

| Service | Required | Used for | Configure |
|---|---|---|---|
| **Audiobookshelf** | ✅ | library, users, progress, import target | wizard / `ABS_*` |
| **Prowlarr** | for searching | Usenet and torrent indexers | wizard / `PROWLARR_*`, `*_INDEXER_IDS` |
| **SABnzbd** | optional | Usenet downloads | wizard / `SABNZBD_*` |
| **qBittorrent** | optional | torrent downloads | wizard / `QBIT_*` |
| **ntfy** | optional | push notifications (browser push works without) | wizard / `NTFY_*` |
| **FlareSolverr** | optional | Goodreads genres, some extensions | wizard / `FLARESOLVERR_URL` |

Secrets can be passed as files (`*_HOST_FILE` in `.env`, mounted read-only) or entered in the UI.
They are never sent back to the browser.

### Running next to other containers

If Prowlarr, SABnzbd & co. live in other Compose projects, add their networks in a `compose.override.yaml` next to
`compose.yaml` (it is ignored by git and loaded automatically):

```yaml
services:
  abw:
    networks: [default, media]
networks:
  media:
    name: my-media-network
    external: true
```

---

## 🧭 How it works

```mermaid
flowchart LR
    ABS[(Audiobookshelf)] -->|library & progress| ABW
    AUD[Audible catalogue] -->|all volumes & dates| ABW
    ABW{{AudioBook Wizarrd}} -->|search| PR[Prowlarr]
    PR --> IDX[(your indexers)]
    ABW -->|download| DL[SABnzbd / qBittorrent]
    DL -->|finished files| CHK[Arrival check]
    CHK -->|passed| LIB[(Library folder)]
    CHK -->|needs a look| REV[Review]
    LIB -->|rescan| ABS
```

1. **Sync** – ABW reads your libraries from Audiobookshelf and asks Audible for every series (respecting a polite request rate).
2. **Gap detection** – volumes Audible knows but your library lacks become *missing*; future ones become *upcoming*.
3. **Search** – for missing volumes ABW queries Prowlarr with several query variants and scores each result
   (title, volume number, author, narrator, language, size, omnibus detection).
4. **Download** – sure results start automatically (if enabled), unsure ones wait under *Review → Select a result*.
5. **Arrival check** – runtime, tags, file integrity and optionally the spoken intro are compared with Audible.
6. **Import** – passed downloads are moved into the library, Audiobookshelf is told to rescan, and the download folder is cleaned up.

Every automatic step can be switched on and off individually, and each series can opt out.

---

## 🧩 Extensions

Usenet and torrent search go through Prowlarr. If you have a source Prowlarr cannot handle, write a small plugin instead of
forking ABW. Put a Python module in the folder mounted at `/plugins`:

```python
# /plugins/my_source.py
from pathlib import Path

class MySource:
    key, name, order = "mysource", "My source", 5
    root = Path("/my-downloads")          # finished downloads as ABW sees them

    def configured(self): return True
    def search(self, queries):            # -> (results, errors)
        return [{"guid": "…", "source": self.key, "title": "Author - Title", "size": 300_000_000}], []
    def refresh(self, guid): ...
    def submit(self, guid, marker): ...    # start download into root/marker, return client id
    def status(self, rows): ...            # {download_id: {"state": "downloading", "progress": 0.4}}
    def can_start(self): return True

def register(abw):
    abw.add_source(MySource())
```

The full interface is documented in [`backend/abw/plugins.py`](backend/abw/plugins.py). Extra Python packages can be installed
at build time with `ABW_EXTRA_PIP`.

---

## ❓ FAQ

<details>
<summary><b>Does ABW change my existing audiobooks?</b></summary>

Only if you switch on import (`ABW_IMPORT_ENABLED=1`) and the related tasks. Replaced files go to a trash folder on the same
disk; nothing is deleted without you pressing a button. Without import, ABW is read-only towards your library.
</details>

<details>
<summary><b>Which languages and Audible regions are supported?</b></summary>

English (audible.com) and German (audible.de) libraries – one Audiobookshelf library per language, each optional.
The interface is available in English and German. More regions are possible; open an issue if you need one.
</details>

<details>
<summary><b>Do I need Usenet or torrents?</b></summary>

No. Without Prowlarr ABW is a very good overview of your series: what is complete, what is missing, what is coming.
</details>

<details>
<summary><b>Can my family use it?</b></summary>

Yes. Create users in *Settings*, or import them from Audiobookshelf so they sign in with their existing password.
Users can browse, rate, keep a To Read list, request books and recommend series to each other; reviewing and tasks stay with
admins. Under *Settings → Recommend* an admin decides who may recommend to whom.
</details>

<details>
<summary><b>Can I expose ABW to the internet?</b></summary>

Only behind a reverse proxy with HTTPS and with sign-in enabled. Without an account ABW refuses requests from outside your network.
</details>

<details>
<summary><b>Where are my settings stored? How do I back up?</b></summary>

Everything lives in `ABW_DATA` (SQLite database `abw.db` plus keys). Back up that folder; `scripts/nightly_backup.py`
shows one way to do it (consistent SQLite backup with rotation).
</details>

---

## 🛠️ Development

```sh
# backend
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
ABW_DATA_DIR=./data .venv/bin/python -m uvicorn abw.api:app --port 8790

# frontend (proxy to :8790)
cd frontend && npm ci && npm run dev
```

Stack: **FastAPI** + **SQLite** (backend), **React 19**, **Vite**, **Tailwind 4**, **TypeScript** (frontend), one Docker image with ffmpeg.

**Translations:** the code is written in German; English lives in `frontend/src/locales/en.json` and
`backend/abw/locales/en.json`. See [`scripts/i18n/README.md`](scripts/i18n/README.md) for how to add or update translations.

**Contributing:** issues and pull requests are welcome. Please keep pull requests focused, add tests for behaviour changes,
and run `pytest`, `npm run build` and `npm run lint` before pushing – CI does the same.

---

## ⚖️ Legal

AudioBook Wizarrd does not host, index or distribute any content. It is a management tool that talks to software you run
yourself. You are responsible for complying with the laws of your country and the terms of the services you use.
Only download audiobooks you are entitled to. ABW is not affiliated with Audible, Amazon or Audiobookshelf.

## 📄 License

[GPL-3.0](LICENSE) – free to use, change and share, as long as derived works stay open too.

---

## 🇩🇪 Deutsch

**AudioBook Wizarrd** ergänzt [Audiobookshelf](https://www.audiobookshelf.org/), ähnlich wie Sonarr für Serien.
Es zeigt jede Hörbuch-Serie mit allen Bänden laut Audible, erkennt fehlende und angekündigte Bände, sucht sie auf Wunsch über
Prowlarr, lädt mit SABnzbd oder qBittorrent, **prüft jeden Download** (Laufzeit, Tags, gesprochene Ansage) und legt ihn sauber in
die Bibliothek.

**Das kann ABW**
- **Hören:** To-Read-Liste je Benutzer mit nächstem Band, Fortschritt über die ganze Serie und voraussichtlichem Ende nach deinem
  Hörtempo; füllt sich beim Hören selbst. Serien anderen Benutzern **weiterempfehlen** (wer wem empfehlen darf, legt der
  Administrator fest), gehörte Bücher mit Sternen **bewerten**, als gehört markieren (wird nach Audiobookshelf übertragen)
- **Bibliothek:** jede Serie mit allen Bänden laut Audible, deutsche und englische Fassung auf einer Karte, andere Sprecher und
  Hörspiele, Einzelbücher je Autor, Filter nach Sprache, Status, Genre, Tag und Wunsch, Autorenübersicht
- **Entdecken:** Vorschläge «Für dich» aus Gehörtem und Bewertungen, Serien deiner Autoren, Audible-Toplisten und freie Suche,
  Goodreads-Regal als Wunschliste
- **Neu:** Serien und Autoren markieren, Meldungen in der App, als Browser-Push oder über ntfy, Erscheinungskalender
- **Rückblick:** Hörverlauf je Woche, Monat und Jahr (Konten, Geräte, Uhrzeiten, liegengebliebene Bücher), Statistik zum Bestand,
  eigene Bewertungen und Topliste je Tag
- **Werkstatt:** Suche über Prowlarr, Download über SABnzbd und qBittorrent, Bewertung jedes Treffers mit Begründung,
  Ankunftsprüfung (Laufzeit, Tags, gesprochene Ansage), Sammelbände aufteilen, Ablage mit Audible-Match, bessere Fassungen
  ersetzen schlechtere, Papierkorb statt Löschen, E-Books, MP3 zu M4B, Kapitel von Audible, nächtliche Prüfung auf Schäden
- **Für Selbsthoster:** ein Docker-Image, SQLite, Setup-Wizard, alle Verbindungen in der Oberfläche einstell- und prüfbar,
  Anmeldung auch mit dem Audiobookshelf-Konto, Oberfläche auf Deutsch und Englisch, hell und dunkel, als App installierbar,
  Erweiterungen für eigene Quellen. Die Automatik ist nach der Einrichtung aus.

**Schnellstart**

```sh
git clone https://github.com/moritzfrey95-beep/audiobook-wizarrd.git
cd audiobook-wizarrd && cp .env.example .env   # Ordner eintragen
docker compose up -d --build
```

Dann `http://<server>:8790` öffnen. Der Setup-Wizard fragt nach Audiobookshelf (Pflicht), alles andere ist optional.
Nach der Einrichtung ist die **Automatik aus**, bis du sie unter *Werkstatt → Aufgaben* einschaltest.
Ordner gibst du so an, wie ABW sie im Container sieht. Spracherkennung (Whisper) gibt es mit `ABW_WHISPER=1`.

ABW stellt keine Inhalte bereit und spricht nur mit Diensten, die du selbst betreibst. Lade nur Hörbücher, die du laden darfst.
Lizenz: GPL-3.0.
