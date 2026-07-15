#!/bin/bash
# install.sh — Install patched maxcube library into Home Assistant
# Supports: Core (venv), Container (Docker), HA OS (Docker)
# Run: sudo bash install.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PATCH_FILES="commander.py cube.py"

# ── 1. Detect installation type ─────────────────────────────────────────

find_maxcube_dir() {
  find /srv /usr /usr/local /home -path "*/maxcube/__init__.py" -not -path "*__pycache__*" 2>/dev/null | head -1 | xargs dirname 2>/dev/null || echo ""
}

detect() {
  # HA Container (Docker) — typical names: homeassistant, hass, core-homeassistant
  for container in homeassistant hass core-homeassistant; do
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx "$container"; then
      echo "docker:$container"
      return
    fi
  done

  # HA Core (venv) — look for maxcube on host filesystem
  local dir
  dir=$(find_maxcube_dir)
  if [ -n "$dir" ]; then
    echo "core:$dir"
    return
  fi

  # HA OS — SSH/console access with docker
  if command -v docker &>/dev/null; then
    local containers
    containers=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i home | head -1)
    if [ -n "$containers" ]; then
      echo "docker:$containers"
      return
    fi
  fi

  echo ""
}

install_core() {
  local dir="$1"
  echo "Found maxcube at: $dir"

  # Backup
  local backup="${dir}/../maxcube.backup.$(date +%Y%m%d%H%M%S)"
  echo "Backing up originals to: $backup"
  mkdir -p "$backup"
  cp "$dir"/*.py "$backup/"

  # Copy patches
  for f in $PATCH_FILES; do
    cp "$SCRIPT_DIR/maxcube/$f" "$dir/$f"
  done
  echo "Patched files installed."

  # Fix ownership
  local user
  user=$(stat -c '%U' "$dir" 2>/dev/null || echo "homeassistant")
  for f in $PATCH_FILES; do
    chown "$user":"$user" "$dir/$f" 2>/dev/null || true
  done

  echo ""
  echo "Restart HA: sudo systemctl restart homeassistant"
}

install_docker() {
  local container="$1"
  echo "Found HA container: $container"

  # Find maxcube path inside container
  local dir
  dir=$(docker exec "$container" find /usr /usr/local -path "*/maxcube/__init__.py" -not -path "*__pycache__*" 2>/dev/null | head -1 | xargs dirname 2>/dev/null || echo "")

  if [ -z "$dir" ]; then
    # Try common paths
    for p in \
      /usr/local/lib/python3.12/site-packages/maxcube \
      /usr/local/lib/python3.11/site-packages/maxcube \
      /usr/lib/python3.12/site-packages/maxcube \
      /usr/lib/python3.11/site-packages/maxcube; do
      if docker exec "$container" test -d "$p" 2>/dev/null; then
        dir="$p"
        break
      fi
    done
  fi

  if [ -z "$dir" ]; then
    echo "Error: could not find maxcube inside container."
    echo "Try manually:"
    echo "  docker exec -it $container find /usr -name 'cube.py' -path '*/maxcube/*'"
    exit 1
  fi

  echo "Found maxcube at: $container:$dir"

  # Backup inside container
  local ts
  ts=$(date +%Y%m%d%H%M%S)
  docker exec "$container" bash -c "mkdir -p ${dir}/../maxcube.backup.${ts} && cp ${dir}/*.py ${dir}/../maxcube.backup.${ts}/" 2>/dev/null || \
    echo "Warning: backup inside container failed (non-critical)"

  # Copy patches via docker cp (need to go to a temp dir because docker cp
  # copies the source file, not content)
  local tmpdir
  tmpdir=$(mktemp -d)
  for f in $PATCH_FILES; do
    cp "$SCRIPT_DIR/maxcube/$f" "$tmpdir/$f"
    docker cp "$tmpdir/$f" "${container}:${dir}/${f}"
  done
  rm -rf "$tmpdir"
  echo "Patched files copied into container."

  echo ""
  echo "Restart HA container:"
  echo "  docker restart $container"
  echo "  # or via HA UI: Settings → System → Restart"
}

# ── 2. Main ─────────────────────────────────────────────────────────────

TYPE=$(detect)

if [ -z "$TYPE" ]; then
  echo "╔══════════════════════════════════════════════════════════╗"
  echo "║  Could not auto-detect Home Assistant installation.     ║"
  echo "╠══════════════════════════════════════════════════════════╣"
  echo "║  Manual installation:                                   ║"
  echo "║                                                        ║"
  echo "║  1. Find maxcube path:                                  ║"
  echo "║     find / -path '*/maxcube/__init__.py'                ║"
  echo "║                                                        ║"
  echo "║  2. Copy patched files:                                 ║"
  echo "║     cp maxcube/commander.py <PFAD>/                     ║"
  echo "║     cp maxcube/cube.py <PFAD>/                         ║"
  echo "║                                                        ║"
  echo "║  3. Restart HA                                          ║"
  echo "╚══════════════════════════════════════════════════════════╝"
  echo ""
  echo "Detected system info:"
  echo "  docker: $(command -v docker &>/dev/null && echo 'available' || echo 'not found')"
  echo "  python3: $(python3 --version 2>/dev/null || echo 'none')"
  exit 1
fi

MODE="${TYPE%%:*}"
VALUE="${TYPE#*:}"

echo "Detected: HA $MODE ($VALUE)"
echo ""

case "$MODE" in
  core) install_core "$VALUE" ;;
  docker) install_docker "$VALUE" ;;
esac

echo ""
echo "───────────────────────────────────────────────"
echo "After restart, add to configuration.yaml:"
echo ""
echo "  maxcube:"
echo "    gateways:"
echo "      - host: 192.168.1.123"
echo "        scan_interval: 30"
echo ""
echo "Repository: https://github.com/Kalrkloss/maxcube-ha-fix"
