# gruenbeck2lox

Schlanker Docker-Container, der eine oder mehrere **Grünbeck-Enthärtungsanlagen** direkt mit einem oder mehreren **Loxone Miniservers** verbindet – ohne Middleware, ohne Cloud-Zwang, rein lokal im Netzwerk.

> **Unterstützte Geräte**  
> · SD-Serie (softliQ SD18, SD23, …) · SC-Serie (softliQ SC18, SC23, …)

---

## Features

- **Live-Durchfluss** – Sobald Wasser fließt, wird `currentFlow` sekündlich (via WebSocket) an Loxone gepusht – ohne Polling-Verzögerung
- **Vollständiges Update alle 30 s** – Restkapazität, Salz, Härte, Tagesverbrauch u. v. m.
- **Push-on-Change + Heartbeat** – Loxone bekommt Werte sofort bei Änderung + spätestens nach konfigurierbarem Intervall
- **UDP Datagramm** – Loxone-Miniserver empfängt Werte nativ über virtuelle UDP-Eingänge
- **Web-UI** – Geräte, Miniserver und Mappings komfortabel verwalten
- **Lokal first** – SC-Serie direkt per HTTP, SD-Serie via myGruenbeck-Cloud-Account (lokale Signalr-WebSocket-Verbindung)
- **Passwörter verschlüsselt** – Loxone- und Cloud-Passwörter werden AES-verschlüsselt in SQLite gespeichert

---

## Voraussetzungen

| Voraussetzung | Details |
|---|---|
| Docker + Docker Compose | v2.x |
| Grünbeck SC-Serie | Anlage über IP direkt erreichbar im LAN |
| Grünbeck SD-Serie | myGruenbeck-Account (E-Mail + Passwort) |
| Loxone Miniserver | Im selben Netzwerk erreichbar |

---

## Installation

### 1. Repository klonen

```bash
git clone https://github.com/YOUR_USERNAME/gruenbeck2lox.git
cd gruenbeck2lox
```

### 2. Datenverzeichnis anlegen

```bash
mkdir docker/data
```

### 3. Container starten

```bash
cd docker
docker compose up -d
```

### 4. Web-UI öffnen

```
http://<docker-host>:8080
```

Beim ersten Start automatisch:  
- Datenbank wird angelegt  
- Ein verschlüsselter Fernet-Key wird in `docker/data/.secret` gespeichert

---

## Konfiguration

### Option A – Web-UI (empfohlen)

Alle Einstellungen (Geräte, Miniserver, Mappings) können vollständig über die Web-UI unter `http://<ip>:8080` verwaltet werden.

### Option B – config.yaml (Ersteinrichtung / Vorkonfiguration)

Eine `config.yaml` in `docker/data/` wird beim Start **einmalig** in die Datenbank importiert.  
→ Vorlage: [`config.example.yaml`](config.example.yaml)

```bash
cp config.example.yaml docker/data/config.yaml
# Datei anpassen, dann Container starten
```

### Umgebungsvariablen (docker-compose.yml)

| Variable | Standard | Beschreibung |
|---|---|---|
| `GRUENBECK2LOX_DATA_DIR` | `/app/data` | Datenpfad (SQLite + .secret) |
| `GRUENBECK2LOX_LOG_LEVEL` | `INFO` | Loglevel: `DEBUG` · `INFO` · `WARNING` |
| `GRUENBECK2LOX_SECRET_KEY` | *(auto)* | Fernet-Key (automatisch generiert, wenn leer) |
| `GRUENBECK2LOX_PORT` | `8080` | HTTP-Port der Web-UI / API |

→ Vorlage: [`.env.example`](.env.example)

---

## Loxone-Einrichtung

### Virtuellen UDP-Datagramm-Eingang anlegen

In Loxone Config für jeden Datenpunkt:

1. **Peripherie → Virtuelle Eingänge → Virtueller UDP-Datagramm-Eingang** anlegen
2. Port: z. B. `7001` (frei wählbar, muss mit gruenbeck2lox-Konfiguration übereinstimmen)
3. Für jeden Wert einen **Befehl** innerhalb des Datagramm-Eingangs anlegen:
   - Bezeichnung: beliebig (z. B. `Durchfluss`)
   - Erkennung: `\r Identifier=currentFlow`  
     (der Bezeichner entspricht dem Loxone-Key aus der Mapping-Konfiguration)

### Verfügbare Datenpunkte

| Key | Beschreibung | Einheit | Update |
|---|---|---|---|
| `currentFlow` | Aktueller Durchfluss | l/min | live (≤1 s) beim Fließen |
| `residualCapacity` | Restkapazität | l | 30 s |
| `residualCapacityM3` | Restkapazität | m³ (3 Dez.) | 30 s |
| `residualCapacityPct` | Restkapazität | % | 30 s |
| `totalCapacity` | Gesamtkapazität | l | 30 s |
| `waterToday` | Wasserverbrauch heute | l | 30 s |
| `waterMonth` | Wasserverbrauch diesen Monat | l | 30 s |
| `waterYear` | Wasserverbrauch dieses Jahr | l | 30 s |
| `saltToday` | Salzverbrauch heute | kg | 30 s |
| `saltMonth` | Salzverbrauch diesen Monat | kg | 30 s |
| `saltYear` | Salzverbrauch dieses Jahr | kg | 30 s |
| `saltRange` | Salzreichweite | Tage | 30 s |
| `salt_quantity` | Salzmenge aktuell | kg | 30 s |
| `water_hardness_in` | Eingangshärte | °dH | 30 s |
| `water_hardness_out` | Ausgangshärte (Setpoint) | °dH | 30 s |
| `next_regeneration` | Nächste Regeneration | Loxone-Timestamp | 30 s |
| `last_regeneration` | Letzte Regeneration | Loxone-Timestamp | 30 s |
| `maintenanceDays` | Tage bis Wartung | Tage | 30 s |
| `hasError` | Fehler aktiv | 0/1 | 30 s |
| `regeneration_status` | Regeneration läuft | 0/1 | 30 s |

> **Hinweis `currentFlow`:** Bei fließendem Wasser werden **zusätzlich zu den regulären 30-s-Paketen** sekündlich Mini-Pakete mit nur `currentFlow=X.XXX` gesendet – die anderen Werte bleiben unangetastet.

---

## Architektur

```
 Grünbeck SC (LAN)
   └── HTTP REST ──────────────────────────────────────────┐
                                                           │
 Grünbeck SD (Cloud)                              gruenbeck2lox (Docker)
   └── myGruenbeck Cloud ─── SignalR WebSocket ──►  FastAPI + Scheduler
                                                           │
                                                   SQLite + Web-UI
                                                   http://<ip>:8080
                                                           │
                                  ┌────────────────────────┘
                                  │
                          Loxone Miniserver
                   UDP Datagramm-Eingang (Port 7001)
```

---

## SD-Serie: myGruenbeck-Cloud-Anbindung

Die SD-Serie unterstützt keine direkte lokale REST-API. gruenbeck2lox verbindet sich daher mit dem **myGruenbeck-Cloud-Account** und nutzt den integrierten **SignalR WebSocket** für Echtzeit-Durchflussdaten.

**Benötigt:**
- myGruenbeck-App-Account (E-Mail + Passwort)
- Die Anlage muss in der myGruenbeck-App registriert sein

**Konfiguration in der Web-UI:**
1. Gerät hinzufügen → Typ `sd` wählen
2. E-Mail und Passwort des myGruenbeck-Accounts eingeben
3. Das Gerät wird automatisch erkannt

---

## Entwicklung (ohne Docker)

```bash
# Abhängigkeiten installieren
python -m venv .venv
.venv\Scripts\activate          # Windows
# oder: source .venv/bin/activate   # Linux/macOS
pip install -r backend/requirements.txt

# Datenpfad setzen
$env:GRUENBECK2LOX_DATA_DIR = "$PWD\dev-data"   # PowerShell
# oder: export GRUENBECK2LOX_DATA_DIR="$PWD/dev-data"   # bash

# Server starten
python -m uvicorn backend.main:app --reload --port 8080
```

- Web-UI: http://localhost:8080  
- API-Docs (Swagger): http://localhost:8080/api/docs

---

## Projektstruktur

```
backend/
├── main.py              # FastAPI App + Lifespan
├── config.py            # Settings (Umgebungsvariablen)
├── scheduler.py         # Hintergrund-Polling + Loxone-Push
├── gruenbeck/
│   ├── client.py        # Gemeinsamer Client-Wrapper
│   ├── cloud_api.py     # SD-Serie: myGruenbeck Cloud + WebSocket
│   ├── sc_api.py        # SC-Serie: lokale REST-API
│   ├── sd_api.py        # SD-Serie: lokale XML-API (Fallback)
│   ├── models.py        # DeviceValues Pydantic-Modell
│   └── parser.py        # XML/JSON-Parsing
├── loxone/
│   └── push.py          # UDP Push an Miniserver
├── api/
│   ├── routes_devices.py
│   ├── routes_loxone.py
│   ├── routes_values.py
│   └── routes_ui.py
└── db/
    ├── database.py      # aiosqlite-Wrapper
    └── migrations.py    # Schema-Setup
frontend/                # Web-UI (Vanilla JS + Pico CSS, kein Build-Step)
docker/
├── Dockerfile
├── docker-compose.yml
└── data/                # Volume: SQLite + .secret (nicht im Repo)
```

---

## Lizenz

MIT – siehe [LICENSE](LICENSE)
