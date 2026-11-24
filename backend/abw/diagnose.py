"""Laufzeitabweichung einordnen: Sammelpack, andere Aufnahme, unvollständig oder nur leicht daneben.

Reine Funktionen ohne Datenbank. Die Ankunftsprüfung (verify) ruft sie auf, damit ein zu langer Download nicht pauschal als
«falsche Ausgabe» gilt, sondern ein Befund mit Zahlen und Ursache bekommt.
"""
from . import scoring


def hm(minutes: float) -> str:
    m = round(minutes)
    return f"{m // 60} h {m % 60:02d} min"


def runtime_finding(minutes: float, want: int, release: str = "", runtimes: list[int] | None = None, source: str = "Audible") -> dict:
    """minutes: Dauer des Downloads, want: Laufzeit des gesuchten Bandes laut Audible, release: Name des Treffers,
    runtimes: Laufzeiten aller Bände der Serie laut Audible. source: «Audible» oder «Bestand» (Dauer des vorhandenen
    Buches, wenn Audible keine Laufzeit kennt). Liefert {'status': ok|warn|fail, 'kind', 'text', 'volumes'}."""
    minutes = round(minutes)
    diff = minutes - want
    text = f"{hm(minutes)}, {'Audible nennt' if source == 'Audible' else 'im Bestand sind es'} {hm(want)} ({diff:+d} min)"
    if abs(diff) <= max(5, want * 0.03):
        return {"status": "ok", "kind": "ok", "text": text, "volumes": 1}
    if abs(diff) <= 60:
        return {"status": "warn", "kind": "close", "text": text + ". Abweichung bis 60 Minuten: bitte prüfen", "volumes": 1}
    ratio = minutes / want
    span = scoring.span(release) if release else None
    multi_named = bool(release) and scoring.is_multi(scoring.plain(release))
    runtimes = [r for r in runtimes or [] if r]
    if ratio >= 1.3 and (span or multi_named or (runtimes and abs(minutes - sum(runtimes)) <= max(30, minutes * 0.1))):
        # Beleg für einen Sammelpack: Bereich oder Sammelwort im Namen, oder die Dauer entspricht der ganzen Serie
        exact = True
        if span:
            n = span[1] - span[0] + 1
        elif runtimes and abs(minutes - sum(runtimes)) <= max(30, minutes * 0.1):
            n = len(runtimes)  # entspricht der ganzen Serie
        else:
            n, exact = max(2, round(ratio)), False
        per = minutes / n
        ref = sum(runtimes) / len(runtimes) if len(runtimes) >= 2 else want  # typischer Band der Serie, nicht nur der gesuchte
        count = f"{n} Bände" if exact else f"etwa {n} Bände"
        head = f"Sammelpack: {hm(minutes)} sind das {ratio:.1f}-Fache von Audibles {hm(want)} für einen Band ({count}, je Band rund {hm(per)})"
        if abs(per - ref) <= max(15, ref * 0.15):
            return {"status": "fail", "kind": "pack", "volumes": n,
                    "text": head + ". Die Länge je Band passt zu Audible; aufgeteilt wird nach Bandnummer, Titel oder Laufzeit."}
        return {"status": "fail", "kind": "pack_other", "volumes": n,
                "text": head + f". Audible nennt je Band rund {hm(ref)}: vermutlich eine andere Aufnahme "
                               "(zum Beispiel ungekürzte Lesung statt Hörspiel, oder Gesamtausgabe mit weiteren Bänden). "
                               "Kein einzelner Band, wird nicht abgelegt."}
    if ratio >= 1.5:
        return {"status": "fail", "kind": "long_unnamed", "volumes": 1,
                "text": f"{text}. Das ist das {ratio:.1f}-Fache, der Name nennt aber keinen Sammelband und die Serie passt nicht dazu: "
                        "entweder ein Sammelband ohne Angabe oder die Laufzeit bei Audible stimmt nicht. Bitte ansehen."}
    if ratio <= 0.85:
        return {"status": "fail", "kind": "short", "volumes": 1,
                "text": f"{text}. Nur {round(ratio * 100)} % der Laufzeit {'laut Audible' if source == 'Audible' else 'im Bestand'}:unvollständig heruntergeladen, gekürzte Fassung oder anderer Band"}
    return {"status": "fail", "kind": "other", "volumes": 1,
            "text": f"{text}. Abweichung über 60 Minuten: andere Aufnahme (Sprecher, Kürzung, Fassung) oder Teile fehlen/sind dazugekommen"}
