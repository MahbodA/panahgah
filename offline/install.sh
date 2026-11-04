#!/usr/bin/env bash
# =============================================================================
#  Panahgah — fully OFFLINE installer (Ubuntu 22.04 / 24.04, run as root)
#
#  No downloads at all: no apt, no pip, no mirrors, no GitHub.
#  Every Python library is bundled inside panahgah-offline.zip (libs/ directory).
#  The only requirement on the server is Python 3.10+ (preinstalled on Ubuntu).
#
#    scp panahgah-offline.zip install.sh root@SERVER_IP:/opt/
#    ssh root@SERVER_IP
#    cd /opt && bash install.sh
#
#  Non-interactive:
#    OWNER_USER=admin OWNER_PASS='StrongPass' OWNER_NAME='My Name' bash install.sh
#
#  Optional environment variables:
#    PORT=8001                  application port (default 8001, nginx is not touched)
#    ZIP=/path/to/zip           kit location (default: next to this script, then /opt)
#    PANAHGAH_DIR=/opt/panahgah install directory
#    RESET_OWNER=1              on re-run, ask for the owner credentials again
#
#  Re-running the script updates the code and keeps all data (users, messages, files).
# =============================================================================
set -euo pipefail

main() {
  APP="${PANAHGAH_DIR:-/opt/panahgah}"
  SERVICE_FILE="/etc/systemd/system/panahgah.service"
  OWNER_USER="${OWNER_USER:-}"
  OWNER_PASS="${OWNER_PASS:-}"
  OWNER_NAME="${OWNER_NAME:-}"

  say() { printf '>> %s\n' "$*"; }
  die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

  [ "$(id -u)" = "0" ] || die "Run as root (sudo -i)."
  command -v systemctl >/dev/null 2>&1 || die "systemd is required."
  command -v python3 >/dev/null 2>&1 || die "Python 3 is not installed on the server."
  python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)' || die "Python 3.10 or newer is required."

  # ---------- Locate the kit ----------
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  ZIP="${ZIP:-}"
  if [ -z "$ZIP" ]; then
    for c in "$HERE/panahgah-offline.zip" "/opt/panahgah-offline.zip"; do
      [ -f "$c" ] && { ZIP="$c"; break; }
    done
  fi
  [ -n "$ZIP" ] && [ -f "$ZIP" ] || die "panahgah-offline.zip not found (put it next to install.sh or in /opt, or set ZIP=...)."

  # ---------- Update or fresh install ----------
  UPDATE=0
  if [ -f "$APP/data/panahgah.db" ] && [ -f "$SERVICE_FILE" ] && [ "${RESET_OWNER:-0}" != "1" ]; then
    UPDATE=1
    say "Existing installation found: updating code, keeping all data."
    [ -n "$OWNER_USER" ] || OWNER_USER="$(sed -n 's/^Environment="OWNER_USERNAME=\(.*\)"$/\1/p' "$SERVICE_FILE" | head -n1)"
    [ -n "$OWNER_USER" ] || OWNER_USER="mahbod"   # application default
    if [ -z "${PORT:-}" ]; then
      PORT="$(grep -oE '0\.0\.0\.0:[0-9]+' "$SERVICE_FILE" | head -n1 | cut -d: -f2 || true)"
    fi
  fi
  PORT="${PORT:-8001}"
  [[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || die "Invalid PORT: $PORT"

  # ---------- Owner prompts (fresh install only) ----------
  if [ "$UPDATE" = "0" ]; then
    while true; do
      [ -z "$OWNER_USER" ] && read -rp "Owner username (English, 3-32 letters/digits/_/.): " OWNER_USER
      [[ "$OWNER_USER" =~ ^[A-Za-z0-9_.]{3,32}$ ]] && break
      echo "Invalid username."; OWNER_USER=""
    done
    if [ -z "$OWNER_PASS" ]; then
      while true; do
        read -rsp "Owner password (at least 6 characters): " OWNER_PASS; echo
        [ "${#OWNER_PASS}" -ge 6 ] || { echo "Password is too short."; OWNER_PASS=""; continue; }
        read -rsp "Confirm password: " P2; echo
        [ "$OWNER_PASS" = "$P2" ] && break
        echo "Passwords do not match."; OWNER_PASS=""
      done
    fi
    [ "${#OWNER_PASS}" -ge 6 ] || die "Password must be at least 6 characters."
    [[ "$OWNER_USER" =~ ^[A-Za-z0-9_.]{3,32}$ ]] || die "Invalid owner username."
    if [ -z "$OWNER_NAME" ]; then read -rp "Owner display name (Enter = username): " OWNER_NAME || true; fi
    OWNER_NAME="${OWNER_NAME:-$OWNER_USER}"
  fi

  # ---------- Extract (data/ is never touched; no unzip needed) ----------
  say "Extracting project"
  systemctl stop panahgah 2>/dev/null || true
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  python3 - "$ZIP" "$TMP" <<'PY'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
z.extractall(sys.argv[2])
PY
  [ -f "$TMP/panahgah/app.py" ] && [ -d "$TMP/panahgah/libs/flask" ] \
    || die "Incomplete ZIP (app.py or libs/ not found). Use the official panahgah-offline.zip."
  mkdir -p "$APP"
  rm -rf "$APP/libs"
  rm -rf "$TMP/panahgah/data" "$TMP/panahgah/venv"
  cp -a "$TMP/panahgah/." "$APP/"

  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"; then
    die "Port $PORT is already in use. Choose another: PORT=8002 bash install.sh"
  fi
  say "Application port: $PORT"

  # ---------- Owner account ----------
  if [ "$UPDATE" = "0" ]; then
    say "Creating owner account"
    ( cd "$APP" && OWNER_USERNAME="$OWNER_USER" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$APP/libs" python3 manage.py admin "$OWNER_USER" "$OWNER_PASS" "$OWNER_NAME" )
  fi

  # ---------- Permissions ----------
  id panahgah >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin panahgah
  chown -R panahgah:panahgah "$APP"

  # ---------- Service ----------
  cat > "$SERVICE_FILE" <<EOT
[Unit]
Description=Panahgah Messenger
After=network.target

[Service]
User=panahgah
Group=panahgah
WorkingDirectory=$APP
Environment="MAX_UPLOAD_MB=3072"
Environment="OWNER_USERNAME=$OWNER_USER"
Environment="PYTHONPATH=$APP/libs"
Environment="PYTHONDONTWRITEBYTECODE=1"
ExecStart=/usr/bin/python3 -m gunicorn -w 3 -k gthread --threads 8 --timeout 900 --no-control-socket -b 0.0.0.0:$PORT app:app
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOT
  systemctl daemon-reload
  systemctl enable panahgah >/dev/null 2>&1
  systemctl restart panahgah

  # ---------- Firewall (only opens the port if ufw is already active) ----------
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -qi '^Status: active'; then
    ufw allow "$PORT"/tcp >/dev/null 2>&1 || true
  fi

  # ---------- Emergency disk space ballast (only on roomy disks) ----------
  if [ ! -f "$APP/BALLAST.bin" ]; then
    FREE_KB="$(df -Pk "$APP" | awk 'NR==2{print $4}')"
    [ "${FREE_KB:-0}" -gt $((12*1024*1024)) ] && { fallocate -l 2G "$APP/BALLAST.bin" 2>/dev/null || true; }
  fi
  [ -f "$APP/BALLAST.bin" ] && chown panahgah:panahgah "$APP/BALLAST.bin" || true

  # ---------- Management command ----------
  cat > /usr/local/bin/panahgah-manage <<EOT
#!/bin/sh
cd $APP && OWNER_USERNAME="$OWNER_USER" PYTHONPATH="$APP/libs" exec python3 manage.py "\$@"
EOT
  chmod +x /usr/local/bin/panahgah-manage

  # ---------- Final check ----------
  say "Waiting for the service"
  OK=0
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if systemctl is-active --quiet panahgah && python3 - "$PORT" <<'PY'
import urllib.request, sys
try:
    urllib.request.urlopen("http://127.0.0.1:%s/" % sys.argv[1], timeout=3)
except Exception:
    sys.exit(1)
PY
    then OK=1; break; fi
    sleep 2
  done
  if [ "$OK" = "1" ]; then
    IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
    echo
    echo "=============================================="
    if [ "$UPDATE" = "1" ]; then echo " Update completed ✓"; else echo " Installation completed ✓"; fi
    echo " URL:        http://${IP:-SERVER_IP}:$PORT"
    [ "$UPDATE" = "0" ] && echo " Username:   $OWNER_USER"
    echo " Management: panahgah-manage list | passwd USER NEWPASS | faststart ..."
    echo " Logs:       journalctl -u panahgah -n 50 --no-pager"
    echo "=============================================="
  else
    die "Service failed to start. Logs: journalctl -u panahgah -n 50 --no-pager"
  fi
}

main "$@"
