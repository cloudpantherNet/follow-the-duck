# Deployment & updates

## Option A — Git (recommended)

On the Pi, once:

```bash
cd ~ && git clone <your-repo-url> follow-the-duck
cd follow-the-duck
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e .
cp .env.example .env && nano .env
```

Update later:

```bash
ssh robot@classbot.local 'cd ~/follow-the-duck && git pull && .venv/bin/pip install -e . -q'
```

(`pip install -e .` is only needed again if `pyproject.toml` changed.)

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
