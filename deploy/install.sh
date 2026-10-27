#!/usr/bin/env bash
# Follow the Duck — one-command installer for the Raspberry Pi.
#
#   cd ~/follow-the-duck && ./deploy/install.sh
#
# Or everything in one line from your laptop:
#   ssh robot@classbot.local 'git clone <repo-url> follow-the-duck 2>/dev/null; cd follow-the-duck && ./deploy/install.sh'
#
# What it does (idempotent — safe to re-run after every `git pull`):
#   1. venv with --system-site-packages (keeps SunFounder picarx/picamera2)
#   2. pip install -e .  (+ the optional wake-word extra, tolerated if it fails)
#   3. downloads openWakeWord models (if the extra installed)
#   4. creates .env from .env.example and prompts for Cloudflare credentials
#   5. installs + enables the systemd unit (starts hands-free listening at boot)
#   6. runs `ftd doctor` and prints how to use the robot
#
# Flags: --no-wake-word   skip the wake-word extra
#        --no-service     skip systemd (manual `ftd listen` use)
#        --yes            non-interactive (no credential prompts)
#
# It never touches system Python packages and never overwrites an existing .env.

set -u

NO_WAKEWORD=0
NO_SERVICE=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --no-wake-word) NO_WAKEWORD=1 ;;
    --no-service)   NO_SERVICE=1 ;;
    --yes)          ASSUME_YES=1 ;;
    *) echo "unknown flag: $arg"; exit 2 ;;
  esac
done

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$REPO_DIR/.venv"
PY="$VENV/bin/python"
RUN_USER="$(id -un)"
cd "$REPO_DIR"

say()  { printf '\n\033[1m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[33mWARN: %s\033[0m\n' "$*"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$*"; exit 1; }

# 1. Virtual environment ------------------------------------------------------
say "Python virtual environment (system-site-packages keeps SunFounder libs)"
command -v python3 >/dev/null || fail "python3 not found"
if [ ! -x "$PY" ]; then
  python3 -m venv --system-site-packages "$VENV" || fail "venv creation failed"
fi
"$PY" -c "import sys; assert sys.version_info >= (3, 9), sys.version" \
  || fail "Python >= 3.9 required"

# 2. Install the package -------------------------------------------------------
say "Installing follow-the-duck into the venv"
# Old pips (< 21.3) cannot editable-install a pyproject-only package.
"$PY" -m pip install -q --upgrade 'pip>=23' 2>/dev/null \
  || warn "pip self-upgrade failed; continuing with the existing pip"
"$PY" -m pip install -q -e . || fail "pip install -e . failed"

# 3. Optional wake word ----------------------------------------------------------
WAKEWORD_OK=0

install_wakeword() {
  # Already working (e.g. re-run after an earlier install)?
  if "$PY" -c "from openwakeword.model import Model" >/dev/null 2>&1; then
    return 0
  fi
  # Preferred: the packaged extra. This fails on Python >= 3.13 because
  # openwakeword pins tflite-runtime on Linux and tflite-runtime has no
  # wheels beyond 3.12 — even though the ONNX path we use never needs it.
  if "$PY" -m pip install -q -e '.[wakeword]' 2>/dev/null; then
    return 0
  fi
  warn "standard openwakeword install failed (its tflite-runtime dependency"
  warn "has no wheel for this Python) — retrying ONNX-only install"
  "$PY" -m pip install -q --no-deps 'openwakeword>=0.6' || return 1
  # openwakeword's actual needs on the ONNX inference path (scikit-learn is
  # imported unconditionally by its package __init__):
  "$PY" -m pip install -q numpy scipy scikit-learn tqdm requests onnxruntime \
    || return 1
  "$PY" -c "from openwakeword.model import Model" >/dev/null 2>&1 || return 1
}

if [ "$NO_WAKEWORD" -eq 0 ]; then
  say "Installing on-device wake word (optional; failure is tolerated)"
  if install_wakeword; then
    say "Downloading openWakeWord models"
    if "$PY" -m follow_the_duck.wakeword --download; then
      WAKEWORD_OK=1
    else
      warn "model download failed (no internet?); re-run install later"
    fi
  else
    warn "wake word unavailable on this platform — hands-free mode will"
    warn "use the loudness gate instead (everything else works normally)"
  fi
else
  say "Skipping wake word (--no-wake-word)"
fi

# 4. Configuration -----------------------------------------------------------------
say "Configuration (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "created .env from .env.example"
fi
if [ "$WAKEWORD_OK" -eq 1 ] && ! grep -q '^FTD_WAKE_WORD_ENABLED=' .env; then
  echo 'FTD_WAKE_WORD_ENABLED=true' >> .env
  echo "enabled wake word in .env (say 'hey jarvis' before commands)"
fi
get_env() { grep "^$1=" .env | head -1 | cut -d= -f2-; }
if [ "$ASSUME_YES" -eq 0 ] && [ -t 0 ]; then
  if [ -z "$(get_env CLOUDFLARE_ACCOUNT_ID)" ]; then
    printf 'Cloudflare account ID (Enter to skip): '
    read -r ACCT
    [ -n "$ACCT" ] && sed -i.bak "s|^CLOUDFLARE_ACCOUNT_ID=.*|CLOUDFLARE_ACCOUNT_ID=$ACCT|" .env
  fi
  if [ -z "$(get_env CLOUDFLARE_API_TOKEN)" ]; then
    printf 'Cloudflare API token, Workers AI permission (hidden; Enter to skip): '
    read -rs TOKEN
    echo
    [ -n "$TOKEN" ] && sed -i.bak "s|^CLOUDFLARE_API_TOKEN=.*|CLOUDFLARE_API_TOKEN=$TOKEN|" .env
  fi
  rm -f .env.bak
fi
if [ -z "$(get_env CLOUDFLARE_ACCOUNT_ID)" ] || [ -z "$(get_env CLOUDFLARE_API_TOKEN)" ]; then
  warn "Cloudflare credentials not set — voice and vision will stay offline"
  warn "until you edit .env (mock/replay demos still work)"
fi

# 5. systemd service ------------------------------------------------------------------
if [ "$NO_SERVICE" -eq 0 ] && command -v systemctl >/dev/null; then
  say "Installing systemd service (hands-free listening at every boot)"
  TMP_UNIT="$(mktemp)"
  sed -e "s|^User=.*|User=$RUN_USER|" \
      -e "s|^WorkingDirectory=.*|WorkingDirectory=$REPO_DIR|" \
      -e "s|^EnvironmentFile=.*|EnvironmentFile=-$REPO_DIR/.env|" \
      -e "s|^ExecStart=.*|ExecStart=$VENV/bin/ftd listen --hands-free|" \
      deploy/ftd.service > "$TMP_UNIT"
  if sudo cp "$TMP_UNIT" /etc/systemd/system/ftd.service \
     && sudo systemctl daemon-reload \
     && sudo systemctl enable --now ftd; then
    echo "service installed and started"
  else
    warn "systemd install failed (no sudo?); run manually: ftd listen --hands-free"
  fi
  rm -f "$TMP_UNIT"
else
  [ "$NO_SERVICE" -eq 1 ] && say "Skipping systemd service (--no-service)" \
                          || warn "systemctl not found; skipping service install"
fi

# 6. Health check ------------------------------------------------------------------------
say "Doctor"
"$VENV/bin/ftd" doctor || true

say "Done"
cat <<EOF
Robot is set up. After every boot it starts listening by itself.

  Speak:            $([ "$WAKEWORD_OK" -eq 1 ] && echo '"hey jarvis" ... beep ... "find the yellow duck"' || echo '"find the yellow duck" (loudness-gated listening)')
  From SSH:         $VENV/bin/ftd find "yellow duck"
  Stop now:         $VENV/bin/ftd stop     (or Ctrl-C / power switch)
  Watch it:         journalctl -u ftd -f   |   $VENV/bin/ftd status
  Update later:     git pull && ./deploy/install.sh

Calibrate the mic gate once: $VENV/bin/ftd doctor --record
EOF
