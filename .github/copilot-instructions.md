## Projektübersicht

**gruenbeck2lox** ist ein schlanker Docker-Container, der eine oder mehrere Grünbeck-Enthärtungsanlagen direkt mit einem oder mehreren Loxone Miniservernverbindet – ohne Middleware wie ioBroker, Home Assistant o. Ä.

**Wichtig: Die Kommunikation mit den Grünbeck-Anlagen erfolgt ausschließlich LOKAL über das Netzwerk – KEINE Cloud-Anbindung!**

Kernprinzipien: **schlank, zuverlässig, lokal, einfach zu konfigurieren**.

---

## Architektur
┌─────────────────┐ Lokale API (HTTP/REST) ┌──────────────────┐
│ Grünbeck SC/SD │ ◄──────────────────────────────► │ gruenbeck2lox │
│ (Enthärtung) │ im selben Netzwerk │ (Docker) │
└─────────────────┘ │ │
│ - Backend (API) │
┌─────────────────┐ HTTP Virtual Input / │ - Web-UI │
│ Loxone │ ◄── UDP Push bei Wertänderung ──── │ - Scheduler │
│ Miniserver │ (oder Polling durch Loxone) │ │
└─────────────────┘ └──────────────────┘

text


### Technologie-Stack

| Komponente | Technologie | Begründung |
|------------|-------------|------------|
| Backend | **Python 3.12+** (FastAPI, uvicorn) | Leichtgewichtig, async-fähig |
| Web-UI | **Vanilla JS + CSS** oder minimal **Alpine.js + Pico CSS** | Schlank, kein Build-Step |
| Datenbank | **SQLite** (via aiosqlite) | Konfiguration & Werte-Puffer; kein extra Service |
| Container | **Python-slim** Docker-Image | Möglichst kleines Image |
| Loxone-Push | **HTTP GET/POST** an Virtual HTTP Inputs + optional **UDP** | Nativ von Loxone unterstützt |

---

## Grünbeck-API-Anbindung (LOKAL)

Die Kommunikation mit Grünbeck-Anlagen erfolgt **ausschließlich lokal** über deren eingebauten Webserver/API im lokalen Netzwerk.

### Lokale Schnittstellen der Grünbeck-Anlagen

#### SD-Serie (ältere Modelle)
- **XML/HTTP-Schnittstelle** auf dem lokalen Netzwerk
- Endpunkt: `http://<anlagen-ip>/mux_http`
- Daten werden als XML geliefert
- Parameter können per GET/POST abgefragt/gesetzt werden

#### SC-Serie (neuere Modelle wie SC18, SC23)
- **REST-API** auf dem lokalen Webserver
- Basis-URL: `http://<anlagen-ip>/`
- Endpunkte für Echtzeitdaten, Parameter, Statistiken
- Teilweise WebSocket-Unterstützung für Live-Updates

### Referenz-Implementierung

Als Referenz für die API-Struktur und Datenpunkte dient der ioBroker-Adapter:

> **Referenz-Repo:** <https://github.com/TA2k/ioBroker.gruenbeck>

**Hinweis:** Das Referenz-Repo nutzt primär die Cloud-API. Für dieses Projekt müssen die **lokalen Endpunkte** verwendet werden, die in der Anlage direkt verfügbar sind.

### Lokale API-Endpunkte (zu ermitteln/dokumentieren)

| Modell | Endpunkt | Methode | Beschreibung |
|--------|----------|---------|--------------|
| SD | `/mux_http` | GET | XML-Daten aller Parameter |
| SC | `/api/realtime` | GET | Echtzeitwerte (JSON) |
| SC | `/api/info` | GET | Geräte-Informationen |
| SC | `/api/measurements` | GET | Messwerte |
| SC | `/api/parameters` | GET/POST | Konfigurationsparameter |

### Wichtige Datenpunkte

- `currentFlow` – Aktueller Durchfluss (l/h)
- `residualCapacity` – Restkapazität (l oder %)
- `totalCapacity` – Gesamtkapazität
- `salt_range` – Salzreichweite (Tage)
- `salt_quantity` – Salzverbrauch (kg)
- `water_hardness_in` – Eingangshärte (°dH)
- `water_hardness_out` – Ausgangshärte (°dH)
- `regeneration_status` – Regenerationsstatus
- `last_regeneration` – Letzte Regeneration (Timestamp)
- `error_code` – Aktueller Fehlercode

---

## Loxone-Anbindung

### Push bei Wertänderung (bevorzugt)

- Bei jeder relevanten Wertänderung einen **HTTP GET oder POST** an den Loxone Miniserver senden.
- Ziel: **Virtueller HTTP-Eingang** (Virtual Input) auf dem Miniserver.
- URL-Schema (konfigurierbar):
http://<user>:<pass>@<miniserver-ip>/dev/sps/io/<input-name>/<value>

text

- Mehrere Miniserver parallel unterstützen.
- Retry-Logik mit exponentiellem Backoff bei Netzwerkfehlern.

### Polling durch Loxone (alternativ)

- Das Backend stellt pro Anlage einen **JSON-Endpunkt** bereit, den der Miniserver per Virtual HTTP Input zyklisch abfragen kann.
- Endpunkt: `GET /api/v1/devices/{device_id}/values`

---

## Projektstruktur
gruenbeck2lox/
├── docker/
│ ├── Dockerfile
│ └── docker-compose.yml
├── backend/
│ ├── main.py # FastAPI-App, Startup, Lifespan
│ ├── config.py # Pydantic Settings (env / config.yaml)
│ ├── gruenbeck/
│ │ ├── init.py
│ │ ├── client.py # HTTP-Client für lokale Grünbeck-API
│ │ ├── sd_api.py # SD-Serie: XML-Parsing
│ │ ├── sc_api.py # SC-Serie: REST/JSON
│ │ ├── models.py # Pydantic-Modelle für Anlagen-Daten
│ │ ├── parser.py # Nachrichten-Parsing (XML/JSON)
│ │ └── discovery.py # (optional) Auto-Discovery im Netzwerk
│ ├── loxone/
│ │ ├── init.py
│ │ ├── push.py # HTTP/UDP Push an Miniserver
│ │ └── models.py # Mapping Grünbeck → Loxone-Inputs
│ ├── api/
│ │ ├── init.py
│ │ ├── routes_devices.py # CRUD Geräte
│ │ ├── routes_loxone.py # CRUD Miniserver-Konfiguration
│ │ ├── routes_values.py # Aktuelle Werte (Polling-Endpunkt)
│ │ └── routes_ui.py # Statische UI-Dateien ausliefern
│ ├── db/
│ │ ├── database.py # SQLite-Verbindung (aiosqlite)
│ │ └── migrations.py # Schema-Setup
│ └── requirements.txt
├── frontend/
│ ├── index.html
│ ├── style.css
│ └── app.js
├── config.example.yaml
├── .env.example
├── .github/
│ └── copilot-instructions.md # ← diese Datei
├── README.md
└── LICENSE

text


---

## Konfiguration

- Primär über **`config.yaml`** (Volume-Mount in Docker).
- Alternativ / ergänzend über **Umgebungsvariablen** (`GRUENBECK2LOX_*`).
- Über die Web-UI konfigurierbar (Schreiben in SQLite; Export als YAML).

### Beispiel `config.yaml`

```yaml
devices:
  - name: "EG Enthärtung"
    type: sc                # sc | sd (Modellreihe)
    host: "192.168.1.50"    # Lokale IP der Grünbeck-Anlage
    port: 80
    poll_interval: 30       # Sekunden

  - name: "Keller Enthärtung"
    type: sd
    host: "192.168.1.51"
    port: 80
    poll_interval: 60

loxone:
  - name: "Miniserver Haus"
    host: "192.168.1.10"
    port: 80
    user: "admin"
    password: "admin"
    push_mode: http         # http | udp
    mappings:
      - gruenbeck_device: "EG Enthärtung"
        gruenbeck_key: "currentFlow"
        loxone_input: "Enthaertung_Durchfluss"
      - gruenbeck_device: "EG Enthärtung"
        gruenbeck_key: "residualCapacity"
        loxone_input: "Enthaertung_Restkapazitaet"
      - gruenbeck_device: "EG Enthärtung"
        gruenbeck_key: "salt_range"
        loxone_input: "Enthaertung_Salzreichweite"

logging:
  level: INFO

scheduler:
  push_on_change: true        # Nur bei Änderung pushen
  push_interval_max: 300      # Spätestens alle 5 Min. pushen (Heartbeat)
Coding-Richtlinien
Allgemein
Sprache im Code: Englisch (Variablen, Kommentare, Docstrings).
Sprache in der UI: Deutsch (Zielgruppe: deutschsprachige Loxone-Nutzer).
Docstrings: Google-Style.
Type-Hints: Überall verwenden (Python ≥ 3.12 Syntax).
Async: Alle I/O-Operationen müssen async sein.
Error-Handling: Niemals Exceptions schlucken; strukturiertes Logging.
Keine Cloud-Dienste – alles läuft lokal im Netzwerk.
Python-spezifisch
Formatter: Ruff (format + lint).
Imports sortieren: ruff check --select I.
Max. Zeilenlänge: 100 Zeichen.
Abhängigkeiten minimal halten; vor jeder neuen Dependency prüfen, ob die Standardbibliothek ausreicht.
Docker
Multi-Stage-Build (Build → Runtime).
Runtime-Image: python:3.12-slim.
Non-root User im Container.
Healthcheck-Endpunkt: GET /api/v1/health.
Volumes: /app/data (SQLite + config.yaml).
Container muss im Host-Netzwerk oder Bridge mit Zugriff auf Grünbeck-IPs laufen.
Frontend / Web-UI
Kein Build-Step erforderlich (kein Webpack, kein npm).
Modernes, responsives Design – Pico CSS oder vergleichbar.
Seiten/Ansichten:
Dashboard – Übersicht aller Anlagen mit Live-Werten.
Geräte – Grünbeck-Anlagen hinzufügen/bearbeiten/löschen (IP-Adresse eingeben).
Loxone – Miniserver und Mappings konfigurieren.
Log – Letzte Ereignisse / Push-Historie.
Kommunikation mit Backend über fetch() → JSON-API.
API-Design
Prefix: /api/v1/
RESTful, JSON-Body, Standard-HTTP-Statuscodes.
Endpunkte:
Methode	Pfad	Beschreibung
GET	/health	Healthcheck
GET	/devices	Alle Grünbeck-Geräte
POST	/devices	Gerät hinzufügen
GET	/devices/{id}	Einzelnes Gerät
PUT	/devices/{id}	Gerät bearbeiten
DELETE	/devices/{id}	Gerät entfernen
GET	/devices/{id}/values	Aktuelle Werte (für Loxone-Polling)
POST	/devices/{id}/test	Verbindung testen
GET	/loxone	Alle Miniserver
POST	/loxone	Miniserver hinzufügen
PUT	/loxone/{id}	Miniserver bearbeiten
DELETE	/loxone/{id}	Miniserver entfernen
GET	/loxone/{id}/mappings	Mappings anzeigen
POST	/loxone/{id}/mappings	Mapping hinzufügen
DELETE	/loxone/{id}/mappings/{mapping_id}	Mapping entfernen
POST	/loxone/{id}/test	Push-Test an Miniserver
GET	/logs	Letzte Log-/Push-Einträge
GET	/discover	(optional) Grünbeck-Geräte im Netzwerk suchen
Sicherheit
Loxone-Passwörter werden verschlüsselt in der SQLite-DB gespeichert (Fernet / AES mit einem Container-Secret).
Die Web-UI ist nur im lokalen Netz erreichbar (kein Auth vorgesehen, aber als optionaler Header X-Api-Key vorbereiten).
Keine Secrets in Logs ausgeben.
Kommunikation mit Grünbeck-Anlagen ist unverschlüsselt (HTTP) – nur im lokalen, vertrauenswürdigen Netzwerk betreiben.
Test-Strategie
pytest + pytest-asyncio für Backend-Tests.
Grünbeck-API mocken (XML/JSON-Fixtures basierend auf echten Antworten).
Loxone-Push mocken (httpx mock / respx).
Mindestens: Unit-Tests für Parser, Push-Logik, API-Routen.