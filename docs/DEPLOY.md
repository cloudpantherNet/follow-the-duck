# Deployment & updates

## One-command install (recommended)

```bash
ssh robot@classbot.local \
  'git clone <repo-url> follow-the-duck 2>/dev/null; cd follow-the-duck && ./deploy/install.sh'
```

`deploy/install.sh` handles the venv, package, wake-word extra + models,
`.env` (with interactive credential prompts), the systemd service, and a
final `ftd doctor`. Idempotent; flags `--no-wake-word`, `--no-service`,
`--yes`. Update later with:

```bash
ssh robot@classbot.local 'cd ~/follow-the-duck && git pull && ./deploy/install.sh --yes'
```

## Manual install (what the script does, step by step)

```bash
cd ~ && git clone <your-repo-url> follow-the-duck
cd follow-the-duck
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
cp .env.example .env && nano .env
```

## Option B — SCP from the Mac (no Git remote needed)

```bash
rsync -av --exclude .venv --exclude .git --exclude .env \
    ./ robot@classbot.local:~/follow-the-duck/
```

`.env` stays on the Pi and is never overwritten or copied into Git.

## Systemd unit: hands-free voice at every boot (no laptop)

```bash
sudo cp deploy/ftd.service /etc/systemd/system/ftd.service
sudo systemctl daemon-reload
sudo systemctl enable --now ftd
journalctl -u ftd -f          # watch the narration
```

The unit runs `ftd listen --hands-free`: after every boot the robot sits in
**IDLE** and listens in short chunks (default 4 s). A *local* loudness gate
(`FTD_VOICE_RMS_THRESHOLD`) discards quiet chunks with **zero cloud calls**;
only chunks that plausibly contain speech go to Whisper, and only a parsed
"find the …" starts a mission. Nothing moves at boot, after a crash, or after
`systemctl restart` — motion always needs a fresh command, and SIGTERM stops
the motors before the loop exits.

Calibrate the gate once per room: `ftd doctor --record` prints the measured
RMS of a 1 s capture — run it once in silence and once while speaking, and
set `FTD_VOICE_RMS_THRESHOLD` between the two.

### Optional: on-device wake word ("hey jarvis")

With the wake word enabled, audio never leaves the Pi until the phrase is
detected locally (openWakeWord, built for the Pi 3) — in a noisy room this
beats the loudness gate on both quota and false triggers:

`./deploy/install.sh` sets all of this up automatically. The manual
equivalent:

```bash
cd ~/follow-the-duck
.venv/bin/pip install -e '.[wakeword]'                     # openwakeword + onnxruntime
.venv/bin/python -m follow_the_duck.wakeword --download    # fetch the models once
echo 'FTD_WAKE_WORD_ENABLED=true' >> .env
.venv/bin/ftd doctor                                       # wake-word check must be OK
sudo systemctl restart ftd
```

On **Python >= 3.13** (Debian 13 Trixie) the pip extra fails: openwakeword
pins `tflite-runtime` on Linux, which has no wheels beyond Python 3.12, even
though our ONNX inference path never uses it. The installer handles this
automatically; manually it is:

```bash
.venv/bin/pip install --no-deps 'openwakeword>=0.6'
.venv/bin/pip install numpy scipy scikit-learn tqdm requests onnxruntime
```

Usage: say **"hey jarvis"** → the robot beeps → say **"find the yellow
duck"**. Only pretrained openWakeWord phrases are available
(`FTD_WAKE_WORD_MODEL`: `hey_jarvis_v0.1`, `alexa_v0.1`, `hey_mycroft_v0.1`);
a custom "hey duck" would require training a model with openWakeWord's
pipeline (future work). Tune `FTD_WAKE_WORD_THRESHOLD` (raise it if it
false-triggers, lower if it seems deaf). If the wake-word backend is missing
or fails to load, the service logs a warning and automatically falls back to
the loudness-gate mode — it never ends up deaf and silent.

While the service runs you can still use SSH at any time:
`ftd stop` (immediate local stop), `ftd status`, or
`sudo systemctl stop ftd` to pause listening entirely. Voice is **not** a
stop path — during a mission the robot is not listening; stop with `ftd
stop`, Enter (if a terminal is attached), or the power switch.

## Credentials hygiene

* `.env` is chmod 600, gitignored, and lives only on the Pi (and your Mac if
  you test online features there).
* The Cloudflare token needs only the **Workers AI** permission — create a
  dedicated token, not a global API key, and rotate it after the demo.
* `ftd doctor` reports credential *presence* only; the token value is never
  printed or logged. Watch quota/usage in the Cloudflare dashboard (Workers AI
  is metered; clef-flash bills input tokens, whisper bills audio minutes).
