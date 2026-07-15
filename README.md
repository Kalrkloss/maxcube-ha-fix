# maxcube-ha-fix

Faster and more reliable MAX! Cube integration for Home Assistant.

> 🇩🇪 [Deutsche Version](README_ger.md)

## Problem

The HA built-in `maxcube` integration polls the cube slowly (default 300s) using close+reconnect:
- Every poll opens and closes a TCP connection (slow, error-prone)
- The cube only allows **one** TCP connection → frequent timeouts
- Status changes (window open/close, thermostat manually adjusted) take minutes to appear
- No persistent connection for push messages

## Solution

This repo provides a patched `maxcube` library with the following changes:

| Change | Original | Patched |
|---|---|---|
| **Connection** | close+reconnect on every poll | Persistent TCP connection |
| **RX thread** | None | Background thread receives push messages instantly |
| **Polling** | Only initial L message on connect | Explicit `l:` command on existing connection |
| **Window sensors** | Only from L messages | Also from C messages (unsolicited config updates) |
| **Close** | Socket dropped without notice | Sends `q:` for clean disconnect |
| **Timeout** | 3s | 5s |

**Result:** Window and thermostat changes are detected within seconds, not minutes.

## Installation

```bash
# 1. Clone repo
git clone https://github.com/Kalrkloss/maxcube-ha-fix.git
cd maxcube-ha-fix

# 2. Auto-install (detects HA Core and HA Container)
sudo bash install.sh

# 3. Restart Home Assistant
# Core: sudo systemctl restart homeassistant
# Container: docker restart homeassistant
```

### HA Core (manual install on Debian/Ubuntu)

```bash
# Find maxcube path
find /srv -path "*/maxcube/__init__.py" -not -path "*__pycache__*"

# Replace files (example path)
cp maxcube/commander.py /srv/homeassistant312/lib/python3.12/site-packages/maxcube/
cp maxcube/cube.py /srv/homeassistant312/lib/python3.12/site-packages/maxcube/

# Restart HA
sudo systemctl restart homeassistant
```

### HA Container (Docker, HA OS, HA Blue/Yellow, Raspberry Pi)

```bash
# Auto-install (detects containers automatically)
sudo bash install.sh

# Or manually:
docker exec -it homeassistant find /usr -path "*/maxcube/cube.py"
# Note the path, then:
docker cp maxcube/commander.py homeassistant:/usr/local/lib/python3.12/site-packages/maxcube/
docker cp maxcube/cube.py homeassistant:/usr/local/lib/python3.12/site-packages/maxcube/
docker restart homeassistant
```

> **Note:** On HA OS (Raspberry Pi image) log in via `ssh` and run `docker exec` from there.
> The container is usually named `homeassistant` or `core-homeassistant`.

### HACS / Custom Component

This patch replaces the maxcube library directly in the HA venv (not a HACS custom component).
The HA integration itself (`homeassistant.components.maxcube`) is unchanged.

## Home Assistant Configuration

`configuration.yaml`:

```yaml
maxcube:
  gateways:
    - host: 192.168.1.123       # Your MAX! Cube IP
      port: 62910               # Default port (optional)
      scan_interval: 30          # Poll interval in seconds
```

With this patch, `scan_interval` can safely be set to 30–60s since there's no reconnect
overhead and the cube keeps its single connection permanently open.

## File Structure

```
maxcube-ha-fix/
├── README.md          # This file
├── README_ger.md      # German version
├── install.sh         # Auto-install script
└── maxcube/
    ├── __init__.py       # unchanged
    ├── buffer.py         # unchanged
    ├── commander.py      # ** PATCHED **
    ├── connection.py     # unchanged
    ├── cube.py           # ** PATCHED **
    ├── deadline.py       # unchanged
    ├── device.py         # unchanged
    ├── message.py        # unchanged
    ├── room.py           # unchanged
    ├── thermostat.py     # unchanged
    ├── wallthermostat.py # unchanged
    └── windowshutter.py  # unchanged
```

## Patches in Detail

### `commander.py`

- **`update()`**: Connection stays open permanently. Sends `l:\r\n` on the existing socket
  and waits for the L response. No more close+reconnect.
- **`__rx_loop()`**: New background thread. Continuously reads from the socket (blocking,
  1s poll timeout) and dispatches messages. Expected replies go to the waiting `__call()`,
  everything else into `__unsolicited_messages`.
- **`__call()`**: Uses `threading.Event` instead of direct `recv()`. Sends message, waits
  for event signal from the RX thread.
- **`__close()`**: Sends `q:` before closing the socket so the cube cleans up the connection.
- **`UPDATE_TIMEOUT`**: 3s → 5s for more stability.

### `cube.py`

- **`parse_c_message()`**: Now also sets `device.is_open` from `data[5]`, not just
  `device.initialized`. This means window status changes are picked up from C messages
  (unsolicited config updates) as well.

## Architecture

```
┌─────────────────────────────────────────────────┐
│  HA maxcube Integration                          │
│                                                  │
│  update() every scan_interval                    │
│    └→ sends l: on existing connection             │
│    └→ RX thread delivers response                 │
│                                                  │
│  RX thread (runs permanently)                    │
│    └→ recv() blocks on socket                    │
│    └→ Push messages → __unsolicited instantly     │
│    └→ Expected replies → Event → __call()         │
│                                                  │
│  TCP connection (single, persistent)             │
│    └→ MAX! Cube port 62910                       │
└─────────────────────────────────────────────────┘
```

## License

GPL v2 — same as the original maxcube library.
