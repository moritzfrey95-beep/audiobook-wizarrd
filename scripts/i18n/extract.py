"""Texte sammeln: Oberfläche (t('…') in frontend/src) und Meldungen des Backends (raise …, HTTPException, setup-Texte).
Aufruf: python3 extract.py <repo> -> schreibt ui-keys.json und py-keys.json neben dieses Skript."""
import ast
import json
import re
import sys
from pathlib import Path

repo = Path(sys.argv[1])
out = Path(__file__).parent

ui = set()
pat = re.compile(r"""\bt\(\s*'((?:[^'\\]|\\.)*)'""")
for f in (repo / "frontend/src").rglob("*.ts*"):
    for m in pat.finditer(f.read_text(encoding="utf-8")):
        ui.add(m.group(1).encode().decode("unicode_escape").encode("latin-1").decode("utf-8") if "\\" in m.group(1) else m.group(1))


def template(node) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        s, i = "", 0
        for v in node.values:
            if isinstance(v, ast.Constant):
                s += v.value
            else:
                s += "{%d}" % i
                i += 1
        return s
    return None


def wanted(s: str | None) -> bool:
    return bool(s) and bool(re.search(r"[A-Za-zÄÖÜäöüß]{3}", s)) and not re.fullmatch(r"[a-z_0-9.{}\- ]+", s)


py = set()
for f in (repo / "backend/abw").glob("*.py"):
    tree = ast.parse(f.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            c = node.exc
            name = c.func.id if isinstance(c.func, ast.Name) else c.func.attr if isinstance(c.func, ast.Attribute) else ""
            args = c.args[1:] if name == "HTTPException" else c.args
            for a in args[:1]:
                s = template(a)
                if wanted(s):
                    py.add(s)
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if isinstance(k, ast.Constant) and k.value in ("text", "detail"):
                    s = template(v)
                    if wanted(s):
                        py.add(s)
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "NO_PASSWORD" for t in node.targets):
            py.add(template(node.value))
py |= {"Bitte anmelden.", "Das darf nur ein Administrator."}

# Weitere Meldungen an die Oberfläche: Aufgaben (Name, Beschreibung, Ergebnis), Fortschritt, Prüfgründe, Treffer-Gründe,
# Hinweise. Gesammelt wird jeder deutsch aussehende Text in Rückgaben, Aufrufen, Tupeln und Wörterbüchern, ausser
# Protokoll, SQL und Pfaden. Übersetzt wird beim Ausliefern (abw/i18n.py, Middleware in api.py).
GERMAN = re.compile(r"[äöüÄÖÜß]|\b(und|oder|nicht|mit|der|die|das|den|dem|ein|eine|einen|ist|sind|von|für|auf|aus|bei|noch|schon|kein|keine|"
                    r"Band|Bände|Bänden|Buch|Bücher|Serie|Serien|Datei|Dateien|Ordner|Laufzeit|Treffer|Titel|Sprecher|Autor|gefunden|geprüft|"
                    r"gelesen|abgelegt|passt|fehlt|gesetzt|lesbar|nennt|KW|Tags|Kapitel|Sitzungen|Konten|Meldungen|neu|Bestand)\b")
SKIP_CALLS = {"info", "warning", "error", "debug", "exception", "execute", "executemany", "print", "compile", "search", "sub", "match",
              "fullmatch", "findall", "split", "join", "replace", "startswith", "endswith", "get", "Path", "strftime", "format"}


def variants(node) -> list[str]:
    """Wie template(), aber Bedingungen aus zwei Texten im f-String ergeben beide Fassungen
    (f"{n} {'Datei' if n == 1 else 'Dateien'}" -> "{0} Datei", "{0} Dateien"), und «a + f"…"» wird zusammengesetzt."""
    def raw(n) -> list[str]:
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            return [n.value.replace("{", "{{").replace("}", "}}")]
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add):
            return [x + y for x in raw(n.left) for y in raw(n.right)]
        if isinstance(n, ast.JoinedStr):
            outs = [""]
            for v in n.values:
                if isinstance(v, ast.Constant):
                    outs = [o + v.value for o in outs]
                elif isinstance(v, ast.FormattedValue) and isinstance(v.value, ast.IfExp) and all(
                        isinstance(x, ast.Constant) and isinstance(x.value, str) for x in (v.value.body, v.value.orelse)):
                    outs = [o + x for o in outs for x in (v.value.body.value, v.value.orelse.value)]
                else:
                    outs = [o + "\0" for o in outs]
            return outs
        return ["\0"]
    if not isinstance(node, (ast.Constant, ast.JoinedStr, ast.BinOp)):
        return []
    out = []
    for r in raw(node):
        if r.replace("\0", "").strip() == "":
            continue
        i = iter(range(100))
        out.append(re.sub("\0", lambda m: "{%d}" % next(i), r.replace("{{", "{").replace("}}", "}")))
    return out


def ui_text(s: str | None) -> bool:
    return (wanted(s) and " " in s.strip() or s in WORDS) and bool(GERMAN.search(s or "")) \
        and not re.match(r"\s*(SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|ALTER|PRAGMA)\b", s) and not s.startswith(("/", "http"))


WORDS = {"Dateien", "Laufzeit", "Tags", "Ansage", "Sprecher", "Sammelband", "Kapitel", "Sprache", "Vergleich mit Bestand", "Titel", "Autor",
         "Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag", "Aufräumen", "Ankunftsprüfung", "Benachrichtigungen",
         "Unbekannt", "Qualitäts-Upgrade", "Handimport"}
for f in (repo / "backend/abw").glob("*.py"):
    tree = ast.parse(f.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        cands = []
        if isinstance(node, ast.Return) and node.value is not None:
            cands.append(node.value)
            if isinstance(node.value, ast.Tuple):
                cands += node.value.elts
        elif isinstance(node, ast.Call):
            fn = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
            if fn not in SKIP_CALLS:
                cands += list(node.args) + [k.value for k in node.keywords]
        elif isinstance(node, (ast.Tuple, ast.List)):
            cands += node.elts
        elif isinstance(node, ast.Dict):
            cands += [v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant) and isinstance(k.value, str)
                      and k.value in ("name", "text", "why", "hint", "error", "message", "reason", "note", "label", "description", "title")]
        elif isinstance(node, ast.Assign):
            cands.append(node.value)
        for c in cands:
            if isinstance(c, ast.IfExp):
                c2 = [c.body, c.orelse]
            else:
                c2 = [c]
            for x in c2:
                for s in variants(x):
                    if s and ui_text(s):
                        py.add(s)

(out / "ui-keys.json").write_text(json.dumps(sorted(ui), ensure_ascii=False, indent=1), encoding="utf-8")
(out / "py-keys.json").write_text(json.dumps(sorted(py), ensure_ascii=False, indent=1), encoding="utf-8")
print("Oberfläche", len(ui), "Zeichen", sum(map(len, ui)))
print("Backend", len(py), "Zeichen", sum(map(len, py)))
