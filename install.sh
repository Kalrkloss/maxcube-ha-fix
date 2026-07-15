#!/bin/bash
# install.sh — Install patched maxcube library into Home Assistant venv
# Run: sudo bash install.sh
set -e

# Detect HA venv path (common locations)
HA_PATHS=(
  "/srv/homeassistant/lib/python3.12/site-packages/maxcube"
  "/srv/homeassistant312/lib/python3.12/site-packages/maxcube"
  "/usr/lib/python3.12/site-packages/maxcube"
  "/usr/local/lib/python3.12/dist-packages/maxcube"
  "/home/homeassistant/.local/lib/python3.12/site-packages/maxcube"
)

MAXCUBE_DIR=""
for p in "${HA_PATHS[@]}"; do
  if [ -d "$p" ]; then
    MAXCUBE_DIR="$p"
    break
  fi
done

if [ -z "$MAXCUBE_DIR" ]; then
  echo "Error: maxcube library not found in any standard location."
  echo "Searching for it..."
  MAXCUBE_DIR=$(find /srv /usr -path "*/maxcube/__init__.py" -not -path "*__pycache__*" 2>/dev/null | head -1 | xargs dirname)
fi

if [ -z "$MAXCUBE_DIR" ]; then
  echo "Error: could not find maxcube library. Is Home Assistant installed?"
  echo "Try: find / -path '*/maxcube/__init__.py' -not -path '*__pycache__*'"
  exit 1
fi

echo "Found maxcube at: $MAXCUBE_DIR"

# Backup originals
BACKUP_DIR="$MAXCUBE_DIR/../maxcube.backup.$(date +%Y%m%d%H%M%S)"
echo "Backing up originals to: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"
cp "$MAXCUBE_DIR"/*.py "$BACKUP_DIR/"

# Install patched files from this repo
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cp "$SCRIPT_DIR/maxcube/commander.py" "$MAXCUBE_DIR/commander.py"
cp "$SCRIPT_DIR/maxcube/cube.py" "$MAXCUBE_DIR/cube.py"

echo "Patched files installed."

# Detect HA service user
HA_USER=$(stat -c '%U' "$MAXCUBE_DIR" 2>/dev/null || echo "homeassistant")
chown "$HA_USER":"$HA_USER" "$MAXCUBE_DIR/commander.py" "$MAXCUBE_DIR/cube.py" 2>/dev/null || true

echo ""
echo "Installation complete. Restart Home Assistant to apply:"
echo "  sudo systemctl restart homeassistant"
echo ""
echo "Then add this to configuration.yaml if not already present:"
echo "  maxcube:"
echo "    gateways:"
echo "      - host: 192.168.1.123"
echo "        scan_interval: 60"
