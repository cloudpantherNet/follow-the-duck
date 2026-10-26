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

## Optional systemd unit (run without a laptop)

```bash
sudo cp deploy/ftd.service /etc/systemd/system/ftd.service
sudo systemctl daemon-reload
sudo systemctl enable --now ftd
journalctl -u ftd -f          # watch the narration
```

The unit starts `ftd listen --loop` in **IDLE**; nothing moves at boot, after
a crash, or after `systemctl restart` — a mission always needs a new command,
and the CLI's SIGTERM handler stops the motors on shutdown. Headless caveat:
with no terminal there is no keyboard push-to-talk, so trigger missions over
SSH (`ftd find "..."`), stop with `ftd stop`, and check `ftd status`.

## Credentials hygiene

* `.env` is chmod 600, gitignored, and lives only on the Pi (and your Mac if
  you test online features there).
* The Cloudflare token needs only the **Workers AI** permission — create a
  dedicated token, not a global API key, and rotate it after the demo.
* `ftd doctor` reports credential *presence* only; the token value is never
  printed or logged. Watch quota/usage in the Cloudflare dashboard (Workers AI
  is metered; clef-flash bills input tokens, whisper bills audio minutes).
