# maxcube-ha-fix

Schnellere und zuverlässigere MAX! Cube-Anbindung für Home Assistant.

## Problem

Die HA-Standard-`maxcube`-Integration pollt den Cube träge (Standard 300s) per close+reconnect:
- Jeder Poll baut eine neue TCP-Verbindung auf und ab (lahm, fehleranfällig)
- Der Cube erlaubt nur **eine** TCP-Verbindung → häufig Timeouts
- Statusänderungen (Fenster auf/zu, Thermostat manuell verstellt) werden erst Minuten später erfasst
- Es gibt keine permanente Verbindung für Push-Nachrichten

## Lösung

Das Repository enthält eine gepatchte `maxcube`-Bibliothek mit folgenden Änderungen:

| Änderung | Original | Patch |
|---|---|---|
| **Verbindung** | close+reconnect bei jedem Poll | Dauerhafte TCP-Verbindung |
| **RX-Thread** | Keiner | Hintergrundthread empfängt Push-Nachrichten sofort |
| **Polling** | Nur initiale L-Nachricht beim Connect | Explizites `l:`-Kommando auf bestehender Verbindung |
| **Fenstersensoren** | Nur aus L-Nachrichten | Auch aus C-Nachrichten (unsolicited config updates) |
| **Close** | Socket einfach fallen lassen | Sendet `q:` für sauberen Verbindungsabbau |
| **Timeout** | 3s | 5s |

**Effekt:** Fenster- und Thermostat-Änderungen werden innerhalb von Sekunden erfasst, nicht erst Minuten später.

## Installation

```bash
# 1. Repo klonen
git clone https://github.com/Kalrkloss/maxcube-ha-fix.git
cd maxcube-ha-fix

# 2. Auto-Installation (erkennt HA Core und HA Container)
sudo bash install.sh

# 3. Home Assistant neustarten
# Core: sudo systemctl restart homeassistant
# Container: docker restart homeassistant
```

### HA Core (manuelle Installation auf Debian/Ubuntu)

```bash
# maxcube-Pfad finden
find /srv -path "*/maxcube/__init__.py" -not -path "*__pycache__*"

# Dateien ersetzen (Beispiel Pfad)
cp maxcube/commander.py /srv/homeassistant312/lib/python3.12/site-packages/maxcube/
cp maxcube/cube.py /srv/homeassistant312/lib/python3.12/site-packages/maxcube/

# HA neustarten
sudo systemctl restart homeassistant
```

### HA Container (Docker, HA OS, HA Blue/Yellow, Raspberry Pi)

```bash
# Auto-Installation (erkennt Container automatisch)
sudo bash install.sh

# Oder manuell:
docker exec -it homeassistant find /usr -path "*/maxcube/cube.py"
# Pfad merken, dann:
docker cp maxcube/commander.py homeassistant:/usr/local/lib/python3.12/site-packages/maxcube/
docker cp maxcube/cube.py homeassistant:/usr/local/lib/python3.12/site-packages/maxcube/
docker restart homeassistant
```

> **Hinweis:** Bei HA OS (Raspberry Pi Image) per `ssh` einloggen und dort `docker exec` ausführen.
> Der Container heißt meist `homeassistant` oder `core-homeassistant`.

### HACS / Custom Component

Der Patch ersetzt direkt die maxcube-Library im HA-venv (kein HACS-Custom-Component).
Die Integration selbst (`homeassistant.components.maxcube`) bleibt unverändert.

## Home Assistant Konfiguration

`configuration.yaml`:

```yaml
maxcube:
  gateways:
    - host: 192.168.1.123       # IP deines MAX! Cube
      port: 62910               # Standard-Port (optional)
      scan_interval: 60         # Polling-Intervall in Sekunden
```

Mit dem Patch kann `scan_interval` bedenkenlos auf 30-60s gesetzt werden, da keine
reconnect-Last mehr anfällt und der Cube seine eine Verbindung dauerhaft hält.

## Dateien

```
maxcube-ha-fix/
├── README.md
├── install.sh
└── maxcube/
    ├── __init__.py       # unverändert
    ├── buffer.py         # unverändert
    ├── commander.py      # ** GEPATCHT **
    ├── connection.py     # unverändert
    ├── cube.py           # ** GEPATCHT **
    ├── deadline.py       # unverändert
    ├── device.py         # unverändert
    ├── message.py        # unverändert
    ├── room.py           # unverändert
    ├── thermostat.py     # unverändert
    ├── wallthermostat.py # unverändert
    └── windowshutter.py  # unverändert
```

## Patches im Detail

### `commander.py`

- **`update()`**: Verbindung bleibt permanent offen. Sendet `l:\r\n` auf bestehendem Socket
  und wartet auf L-Antwort. Kein close+reconnect mehr.
- **`__rx_loop()`**: Neuer Hintergrundthread. Liest permanent vom Socket (blockierend, 1s
  Poll-Timeout) und verteilt Nachrichten. Erwartete Antworten gehen an den wartenden
  `__call()`, der Rest in `__unsolicited_messages`.
- **`__call()`**: Nutzt `threading.Event` statt direktem recv(). Sendet Nachricht, wartet
  auf Event-Signal vom RX-Thread.
- **`__close()`**: Sendet `q:` vor dem Socket-Close, damit der Cube die Verbindung sauber abbaut.
- **`UPDATE_TIMEOUT`**: 3s → 5s für mehr Stabilität.

### `cube.py`

- **`parse_c_message()`**: Setzt jetzt auch `device.is_open` aus `data[5]`, nicht nur
  `device.initialized`. Dadurch werden Fenster-Statusänderungen auch aus C-Nachrichten
  (unsolicited config updates) erfasst.

## Funktionsweise

```
┌─────────────────────────────────────────────────┐
│  HA maxcube Integration                          │
│                                                  │
│  update() alle 60s                               │
│    └→ sendet l: auf bestehender Verbindung        │
│    └→ RX-Thread liefert Antwort                   │
│                                                  │
│  RX-Thread (läuft permanent)                     │
│    └→ recv() blockiert auf Socket                │
│    └→ Push-Nachrichten sofort in __unsolicited   │
│    └→ Erwartete Antworten → Event → __call()     │
│                                                  │
│  TCP-Verbindung (eine, dauerhaft)                │
│    └→ MAX! Cube Port 62910                       │
└─────────────────────────────────────────────────┘
```

## Lizenz

GPL v2 — wie die originale maxcube-Bibliothek.
