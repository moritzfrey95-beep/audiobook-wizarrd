"""Deutsche Texte mit DeepL ins Englische übersetzen. Platzhalter {{v0}} und {0} bleiben unberührt (als XML-Tag geschützt).
Aufruf: python3 deepl.py <schluesseldatei> <keys.json> <ziel.json>
Vorhandene Übersetzungen im Ziel bleiben, nur fehlende werden geholt."""
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

key = Path(sys.argv[1]).read_text().strip()
keys = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
target = Path(sys.argv[3])
done = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
host = "https://api-free.deepl.com" if key.endswith(":fx") else "https://api.deepl.com"
PH = re.compile(r"\{\{\w+\}\}|\{\d+\}")
GLOSSARY_HINT = ""  # Begriffe werden nach der Übersetzung vereinheitlicht (siehe fix())


def protect(s: str) -> str:
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return PH.sub(lambda m: f'<x v="{m.group(0)}"/>', s)


def restore(s: str) -> str:
    s = re.sub(r'<x v="([^"]+)"\s*/>', lambda m: m.group(1), s)
    return s.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


FIX = [  # einheitliche Begriffe für ABW
    (r"\bAudio ?books?hel[fv]\b", "Audiobookshelf"), (r"\bAudio books\b", "Audiobooks"), (r"\bTo-Read\b", "To Read"),
]


def fix(s: str) -> str:
    for a, b in FIX:
        s = re.sub(a, b, s)
    return s


todo = [k for k in keys if k not in done]
print(f"{len(todo)} von {len(keys)} zu übersetzen")
for i in range(0, len(todo), 40):
    chunk = todo[i:i + 40]
    data = urllib.parse.urlencode([("source_lang", "DE"), ("target_lang", "EN-GB"), ("tag_handling", "xml"), ("ignore_tags", "x"),
                                   ("preserve_formatting", "1"), ("context", "User interface of AudioBook Wizarrd, a self-hosted app that "
                                    "manages audiobook series for Audiobookshelf (search via Prowlarr, download, check, import).")]
                                  + [("text", protect(k)) for k in chunk]).encode()
    req = urllib.request.Request(host + "/v2/translate", data=data, headers={"Authorization": f"DeepL-Auth-Key {key}"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                res = json.load(r)["translations"]
            break
        except urllib.error.HTTPError as e:
            if e.code in (429, 456, 503) and attempt < 4:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    for k, tr in zip(chunk, res):
        en = fix(restore(tr["text"]))
        if sorted(PH.findall(en)) != sorted(PH.findall(k)):
            print("Platzhalter verändert:", k, "->", en)
            continue
        done[k] = en
    target.write_text(json.dumps(dict(sorted(done.items())), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(min(i + 40, len(todo)), "fertig")
