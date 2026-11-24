# Übersetzungen

Der Code schreibt Deutsch. Englisch steht in `frontend/src/locales/en.json` (Oberfläche, Schlüssel = deutscher Text) und
`backend/abw/locales/en.json` (Meldungen; Werte als `{0}`, `{1}` …).

1. Texte sammeln: `python3 scripts/i18n/extract.py .` (schreibt `ui-keys.json` und `py-keys.json` nach `scripts/i18n/`)
2. Fehlende Übersetzungen mit DeepL holen (Schlüssel in einer Datei, DeepL API Free genügt):
   - `python3 scripts/i18n/deepl.py <schluessel.txt> scripts/i18n/ui-keys.json frontend/src/locales/en.json`
   - `python3 scripts/i18n/deepl.py <schluessel.txt> scripts/i18n/py-keys.json backend/abw/locales/en.json`
3. Ergebnis durchsehen. Vorhandene Übersetzungen werden nicht überschrieben, von Hand verbessern ist also möglich.

Neue Texte in der Oberfläche immer mit `t('…')` aus `src/i18n.ts` schreiben, Werte als `t('{{n}} Bände', { n })`.
