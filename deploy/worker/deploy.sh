#!/usr/bin/env bash
# One-command deploy of the Follow the Duck dashboard Worker.
#
#   cd deploy/worker && ./deploy.sh
#
# Requires Node 18+. Authenticates via `wrangler login` (browser) the first
# time, or non-interactively with CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID
# in the environment (token needs Workers Scripts:Edit + Workers KV:Edit).
#
# What it does (idempotent):
#   1. creates the FTD_LOG KV namespace if wrangler.toml still has the
#      placeholder, and writes the real id into wrangler.toml
#   2. deploys the Worker
#   3. sets the FTD_TOKEN secret (generates one unless FTD_TOKEN is set)
#   4. prints the exact .env lines for the robot
#
# After the first run, COMMIT the updated wrangler.toml — then any pipeline
# (e.g. Cloudflare Workers Builds) can redeploy with just `npm run deploy`.

set -euo pipefail
cd "$(dirname "$0")"

say() { printf '\n\033[1m==> %s\033[0m\n' "$*"; }

command -v npx >/dev/null || { echo "Node.js (npx) is required"; exit 1; }

# 1. KV namespace ------------------------------------------------------------
if grep -q REPLACE_WITH_YOUR_KV_NAMESPACE_ID wrangler.toml; then
  say "Creating KV namespace FTD_LOG"
  out="$(npx wrangler kv namespace create FTD_LOG 2>&1)" || {
    echo "$out"; exit 1; }
  id="$(printf '%s' "$out" | grep -oE '[0-9a-f]{32}' | head -1)"
  [ -n "$id" ] || { echo "could not parse namespace id from:"; echo "$out"; exit 1; }
  sed -i.bak "s/REPLACE_WITH_YOUR_KV_NAMESPACE_ID/$id/" wrangler.toml && rm -f wrangler.toml.bak
  echo "wrangler.toml updated with KV id $id — commit this change."
else
  say "KV namespace already configured in wrangler.toml"
fi

# 2. Deploy --------------------------------------------------------------------
say "Deploying Worker"
deploy_out="$(npx wrangler deploy 2>&1)" || { echo "$deploy_out"; exit 1; }
echo "$deploy_out"
url="$(printf '%s' "$deploy_out" | grep -oE 'https://[a-z0-9.-]+\.workers\.dev' | head -1)"

# 3. Secret ---------------------------------------------------------------------
if npx wrangler secret list 2>/dev/null | grep -q '"FTD_TOKEN"'; then
  say "FTD_TOKEN secret already set (keeping it)"
  token="<your existing FTD_TOKEN>"
else
  say "Setting FTD_TOKEN secret"
  if [ -n "${FTD_TOKEN:-}" ]; then
    token="$FTD_TOKEN"
  else
    token="$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    echo "generated a random token"
  fi
  printf '%s' "$token" | npx wrangler secret put FTD_TOKEN
fi

# 4. Summary ----------------------------------------------------------------------
say "Done — robot configuration"
cat <<EOF
Add to the robot's .env (then: ftd doctor --online to verify):

  FTD_TELEMETRY_URL=${url:-https://<worker>.workers.dev}
  FTD_TELEMETRY_TOKEN=$token

Dashboard UI: ${url:-<worker URL>}  (enter the same token in the page)
EOF
