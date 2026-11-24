# LichtBlick ÖkoStrom Dynamic für Home Assistant

Custom Integration, die die viertelstündlichen Strompreise des dynamischen Tarifs
**ÖkoStrom Dynamic** von LichtBlick aus deinem Kundenkonto liest und als Sensoren
in Home Assistant bereitstellt – z. B. für das Energie-Dashboard und zur
Kostenberechnung.

> **Inoffiziell.** Die Integration nutzt dieselbe Schnittstelle wie die
> LichtBlick-App (Azure-AD-B2C-Login + GraphQL unter `graph.lichtblick.de`).
> LichtBlick bietet dafür keine offizielle API an; die Schnittstelle kann sich
> jederzeit ändern. Endpunkte und Abfragen basieren auf dem MIT-lizenzierten
> Projekt [chloepriceless/Lichtblick-Lokal-Preis](https://github.com/chloepriceless/Lichtblick-Lokal-Preis).
> Nicht verbunden mit LichtBlick SE.

## Installation

### HACS (benutzerdefiniertes Repository)
1. HACS → ⋮ → *Benutzerdefinierte Repositories* →
   `https://github.com/dirkkohnen/ha-lichtblick` eintragen, Typ *Integration*.
2. „LichtBlick“ installieren und Home Assistant neu starten.

### Manuell
1. Den Ordner `custom_components/lichtblick` nach
   `<config>/custom_components/lichtblick` kopieren.
2. Home Assistant neu starten.

### Einrichtung
*Einstellungen → Geräte & Dienste → Integration hinzufügen → LichtBlick*.
Gib E-Mail und Passwort deiner LichtBlick-App ein. Hat das Konto mehrere
Stromverträge, wählst du anschließend einen aus (für jeden Vertrag kann ein
eigener Eintrag angelegt werden). Lehnt LichtBlick das Passwort später ab,
startet Home Assistant automatisch die erneute Anmeldung.

## Entitäten

| Sensor | Einheit | Hinweis |
|---|---|---|
| Aktueller Preis | €/kWh | wechselt alle 15 Minuten, mit Langzeitstatistik |
| Nächster Preis | €/kWh | Preis des folgenden Zeitfensters |
| Durchschnitts-/Niedrigster/Höchster Preis heute | €/kWh | |
| Zeitpunkt niedrigster/höchster Preis heute | Zeitstempel | Beginn des Zeitfensters |
| Beginn/Ende günstigstes Zeitfenster heute | Zeitstempel | von LichtBlick berechnet, standardmäßig deaktiviert |
| Durchschnitts-/Niedrigster/Höchster Preis morgen, Zeitpunkt niedrigster Preis morgen | €/kWh / Zeitstempel | *nicht verfügbar*, bis LichtBlick die Preise veröffentlicht (meist nachmittags) |
| Preis-Zeitfenster (Diagnose) | Anzahl | Attribute `prices_today` / `prices_tomorrow` mit allen Slots für Diagramme; werden nicht im Recorder gespeichert |

Abruf: stündlich; ab 13 Uhr alle 15 Minuten, solange die Preise für morgen
fehlen. Der aktuelle Preis wird zu jeder Viertelstunde ohne zusätzlichen
API-Aufruf weitergeschaltet.

## Aktion `lichtblick.get_prices`

```yaml
action: lichtblick.get_prices
data:
  config_entry: <ID des Eintrags>   # im UI per Auswahlfeld
  day: tomorrow                     # today | tomorrow | all
response_variable: preise
```
Antwort: `{"prices": [{"start": "...", "end": "...", "price": 0.2345}, ...]}`
(Preis in €/kWh).

## Stromkosten berechnen

**Energie-Dashboard:** *Einstellungen → Dashboards → Energie → Netzbezug*,
deinen Zähler-Sensor (kWh, `total_increasing`) wählen und bei den Kosten
*„Eine Entität mit dem aktuellen Preis verwenden“* →
`sensor.<vertrag>_aktueller_preis`. Home Assistant multipliziert dann jeden
Verbrauchszuwachs mit dem gerade gültigen Viertelstundenpreis.

Damit die Zuordnung zu den 15-Minuten-Preisen stimmt, sollte der Zählersensor
mindestens viertelstündlich aktualisieren (Smart-Meter-Auslesung, Shelly o. Ä.).

Der monatliche **Grundpreis** ist nicht enthalten – bei Bedarf als fester Betrag
ergänzen.

## Bekannte Einschränkungen

- Ob `valueInCents` aus der App alle Preisbestandteile (Netzentgelte,
  Umlagen, MwSt.) enthält, ist von LichtBlick nicht dokumentiert. Werte um
  20–30 ct/kWh deuten auf den Brutto-Arbeitspreis hin, wie ihn die App zeigt.
  Gleiche die Werte einmal mit der App/Rechnung ab.
- Zu viele Fehl-Logins drosselt LichtBlick (HTTP 429); die Integration
  meldet das und versucht es später erneut.

## Entwicklung

```bash
pip install -r requirements_test.txt
pytest
ruff check . && ruff format --check .
```

## Lizenz

MIT – siehe [LICENSE](LICENSE).
