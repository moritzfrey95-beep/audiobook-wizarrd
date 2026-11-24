#!/usr/bin/env python3
"""Nächtliche Sicherung des AudioBook Wizarrd auf das NAS.

1. Konsistente Kopie der Datenbank (SQLite-Backup, auch im Betrieb sicher), geprüft und gepackt nach
   <Ziel>/db/daily/abw-JJJJMMTT.sqlite3.gz (14 Tage); sonntags zusätzlich nach <Ziel>/db/weekly/ (8 Wochen).
2. Alle Branches zum Remote «nas» pushen (und zu «github», sobald es das Repo gibt).

Gedacht als Timer des Benutzers (systemctl --user, siehe scripts/README.md), braucht kein sudo.
Schlägt die Datenbanksicherung fehl, endet das Skript mit Fehler; ein fehlgeschlagener Push wird nur gemeldet.
"""
import argparse
import datetime as dt
import gzip
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

HOME = Path.home()
DATED = re.compile(r"^abw-(\d{8})\.sqlite3\.gz$")


def prune(folder: Path, oldest: dt.date) -> None:
    for p in folder.iterdir():
        m = DATED.match(p.name)
        if m and dt.datetime.strptime(m.group(1), "%Y%m%d").date() < oldest:
            p.unlink()


def atomic(final: Path, fill) -> None:
    temp = final.with_name(final.name + ".part")
    try:
        fill(temp)
        os.replace(temp, final)
    finally:
        temp.unlink(missing_ok=True)


def backup_db(db: Path, target: Path, today: dt.date) -> Path:
    daily, weekly = target / "db/daily", target / "db/weekly"
    daily.mkdir(parents=True, exist_ok=True)
    weekly.mkdir(parents=True, exist_ok=True)
    final = daily / f"abw-{today:%Y%m%d}.sqlite3.gz"
    with tempfile.TemporaryDirectory() as work:
        plain = Path(work) / "copy.sqlite3"
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=60)
        dst = sqlite3.connect(plain)
        try:
            src.backup(dst)
            dst.commit()
            check = dst.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"Kopie fehlerhaft: {check}")
        finally:
            dst.close()
            src.close()

        def pack(temp: Path) -> None:
            with plain.open("rb") as a, gzip.open(temp, "wb") as b:
                shutil.copyfileobj(a, b)
        atomic(final, pack)
    if today.weekday() == 6:
        atomic(weekly / final.name, lambda temp: shutil.copyfile(final, temp))
    prune(daily, today - dt.timedelta(days=14))
    prune(weekly, today - dt.timedelta(weeks=8))
    return final


def push(repo: Path) -> list[str]:
    notes = []
    remotes = subprocess.run(["git", "-C", str(repo), "remote"], capture_output=True, text=True).stdout.split()
    for remote in ("nas", "github"):
        if remote not in remotes:
            continue
        r = subprocess.run(["git", "-C", str(repo), "push", "--quiet", remote, "--all"], capture_output=True, text=True, timeout=300,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=20"})
        notes.append(f"Push {remote}: {'ok' if r.returncode == 0 else 'fehlgeschlagen (' + (r.stderr.strip().splitlines() or ['?'])[-1][:120] + ')'}")
    return notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=HOME / "audiobook-wizarrd/data/abw.db")
    ap.add_argument("--repo", type=Path, action="append", help="Git-Repo, dessen Branches gepusht werden; mehrfach möglich")
    ap.add_argument("--target", type=Path, required=True, help="Zielordner, z. B. auf einem NAS")
    ap.add_argument("--no-push", action="store_true")
    a = ap.parse_args()
    if not a.target.is_dir():
        print(f"Ziel {a.target} fehlt (NAS nicht eingebunden?)", file=sys.stderr)
        return 1
    final = backup_db(a.db, a.target, dt.date.today())
    print(f"Datenbank gesichert: {final} ({final.stat().st_size / 1e6:.1f} MB)")
    if not a.no_push:
        try:
            for repo in a.repo or [HOME / "audiobook-wizarrd"]:
                for note in push(repo):
                    print(f"{repo.name}: {note}")
        except Exception as exc:
            print(f"Push nicht möglich: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
