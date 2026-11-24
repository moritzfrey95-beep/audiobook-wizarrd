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

(out / "ui-keys.json").write_text(json.dumps(sorted(ui), ensure_ascii=False, indent=1), encoding="utf-8")
(out / "py-keys.json").write_text(json.dumps(sorted(py), ensure_ascii=False, indent=1), encoding="utf-8")
print("Oberfläche", len(ui), "Zeichen", sum(map(len, ui)))
print("Backend", len(py), "Zeichen", sum(map(len, py)))
