#!/usr/bin/env bash
# =============================================================================
#  Panahgah — one-click ONLINE installer (Ubuntu 22.04 / 24.04 / Debian, root)
#
#  Install:
#    bash <(curl -fsSL https://raw.githubusercontent.com/MahbodA/panahgah/main/install.sh)
#
#  Non-interactive:
#    OWNER_USER=admin OWNER_PASS='StrongPass' OWNER_NAME='My Name' \
#      bash <(curl -fsSL https://raw.githubusercontent.com/MahbodA/panahgah/main/install.sh)
#
#  Optional environment variables:
#    PORT=8001               application port (default 8001, nginx is not touched)
#    PANAHGAH_DIR=/opt/panahgah   install directory
#    PANAHGAH_REPO=user/repo      GitHub repository to download from
#    PANAHGAH_REF=main            branch or tag to install
#    PANAHGAH_SRC=/path/to/repo   use a local checkout instead of downloading
#    PIP_INDEX_URL=...            PyPI mirror to use (default: PyPI, then a fallback mirror)
#    RESET_OWNER=1                on re-run, ask for the owner credentials again
#
#  Re-running the script updates the code and keeps all data (users, messages, files).
#  No internet during the outage? Use the offline kit instead: offline/ in the repository.
# =============================================================================
set -euo pipefail

main() {
  REPO="${PANAHGAH_REPO:-MahbodA/panahgah}"
  REF="${PANAHGAH_REF:-main}"
  APP="${PANAHGAH_DIR:-/opt/panahgah}"
  SERVICE_FILE="/etc/systemd/system/panahgah.service"
  FALLBACK_PIP_MIRROR="https://mirror-pypi.runflare.com/simple"
  OWNER_USER="${OWNER_USER:-}"
  OWNER_PASS="${OWNER_PASS:-}"
  OWNER_NAME="${OWNER_NAME:-}"

  say()  { printf '>> %s\n' "$*"; }
  die()  { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

  [ "$(id -u)" = "0" ] || die "Run as root (sudo -i)."
  command -v systemctl >/dev/null 2>&1 || die "systemd is required."

  # Make prompts work even when the script is piped (curl ... | bash).
  if [ ! -t 0 ] && { : </dev/tty; } 2>/dev/null; then exec </dev/tty; fi


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

  # ---------- System prerequisites ----------
  apt_install() {
    command -v apt-get >/dev/null 2>&1 || die "apt-get not found. Install manually: $*  (or use the offline kit)."
    say "Installing system packages: $*"
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" >/dev/null 2>&1 \
      || { apt-get update -qq >/dev/null 2>&1 || true
           DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" >/dev/null 2>&1; } \
      || die "apt could not install: $*. Check the server's package mirror, or use the offline kit."
  }
  [ -n "${PANAHGAH_SRC:-}" ] || command -v curl >/dev/null 2>&1 || apt_install curl ca-certificates
  command -v tar >/dev/null 2>&1 || apt_install tar
  command -v python3 >/dev/null 2>&1 || apt_install python3
  python3 -c 'import sys;sys.exit(0 if sys.version_info>=(3,10) else 1)' || die "Python 3.10 or newer is required."
  python3 -c 'import venv, ensurepip' >/dev/null 2>&1 || apt_install python3-venv python3-pip

  # ---------- Get the code ----------
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  if [ -n "${PANAHGAH_SRC:-}" ]; then
    SRC="$PANAHGAH_SRC"
    say "Using local source: $SRC"
  else
    say "Downloading $REPO@$REF"
    curl -fsSL --retry 3 --connect-timeout 15 "https://codeload.github.com/$REPO/tar.gz/$REF" | tar -xz -C "$TMP" \
      || die "Download failed. Check PANAHGAH_REPO/PANAHGAH_REF and that github.com is reachable from this server."
    SRC="$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -n1)"
  fi
  [ -f "$SRC/app/app.py" ] || die "Invalid source: app/app.py not found."

  # ---------- Install files (data/ and venv/ are never touched) ----------
  systemctl stop panahgah 2>/dev/null || true
  mkdir -p "$APP"
  cp -a "$SRC/app/." "$APP/"
  find "$APP" -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true

  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]$PORT\$"; then
    die "Port $PORT is already in use. Choose another: PORT=8002 bash install.sh"
  fi

  # ---------- Python environment ----------
  say "Preparing Python environment"
  [ -x "$APP/venv/bin/python" ] || python3 -m venv "$APP/venv"
  pip_install() { "$APP/venv/bin/pip" install --disable-pip-version-check -q --timeout 30 --retries 2 "$@" -r "$APP/requirements.txt"; }
  if [ -n "${PIP_INDEX_URL:-}" ]; then
    pip_install || die "pip install failed using PIP_INDEX_URL=$PIP_INDEX_URL"
  else
    pip_install || { say "PyPI unreachable, trying fallback mirror"; pip_install -i "$FALLBACK_PIP_MIRROR" \
      || die "pip install failed. Use PIP_INDEX_URL=<mirror> or the offline kit."; }
  fi

  # ---------- Owner account ----------
  if [ "$UPDATE" = "0" ]; then
    say "Creating owner account"
    ( cd "$APP" && OWNER_USERNAME="$OWNER_USER" PYTHONDONTWRITEBYTECODE=1 "$APP/venv/bin/python" manage.py admin "$OWNER_USER" "$OWNER_PASS" "$OWNER_NAME" )
  fi

  # ---------- Permissions ----------
  id panahgah >/dev/null 2>&1 || useradd -r -s /usr/sbin/nologin panahgah
  chown -R panahgah:panahgah "$APP"

  # ---------- Service ----------
  NOCTL=""
  "$APP/venv/bin/gunicorn" --help 2>/dev/null | grep -q -- '--no-control-socket' && NOCTL="--no-control-socket"
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
Environment="PYTHONDONTWRITEBYTECODE=1"
ExecStart=$APP/venv/bin/gunicorn -w 3 -k gthread --threads 8 --timeout 900 $NOCTL -b 0.0.0.0:$PORT app:app
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

  # ---------- Management command ----------
  cat > /usr/local/bin/panahgah-manage <<EOT
#!/bin/sh
cd $APP && OWNER_USERNAME="$OWNER_USER" exec $APP/venv/bin/python manage.py "\$@"
EOT
  chmod +x /usr/local/bin/panahgah-manage
  [ -f "$APP/BALLAST.bin" ] && chown panahgah:panahgah "$APP/BALLAST.bin" || true

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
