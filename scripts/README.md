# Nächtliche Sicherung

`nightly_backup.py` sichert die Datenbank geprüft und gepackt in den Zielordner `--target` (Unterordner `db/`)
(täglich 14 Tage, sonntags 8 Wochen) und pusht alle Branches zum Remote `nas`, falls vorhanden.

In `audiobook-wizarrd-backup.service` zuerst `--target` auf den eigenen Sicherungsordner setzen.

Timer des Benutzers einrichten (kein sudo nötig, Linger ist an):

    mkdir -p ~/.config/systemd/user && cp ~/audiobook-wizarrd/scripts/audiobook-wizarrd-backup.{service,timer} ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now audiobook-wizarrd-backup.timer

Prüfen: `systemctl --user list-timers` und `systemctl --user status audiobook-wizarrd-backup.service`.
