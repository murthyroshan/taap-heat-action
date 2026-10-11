#!/usr/bin/env bash
# Point the Telegram bot at the deployed BotFunction URL.
# Usage: TELEGRAM_TOKEN=... TELEGRAM_SECRET=... scripts/set_webhook.sh [stack-name]
set -euo pipefail
STACK="${1:-taap}"
: "${TELEGRAM_TOKEN:?set TELEGRAM_TOKEN}" "${TELEGRAM_SECRET:?set TELEGRAM_SECRET}"
URL=$(aws cloudformation describe-stacks --stack-name "$STACK" \
  --query "Stacks[0].Outputs[?OutputKey=='BotWebhookUrl'].OutputValue" --output text)
echo "Webhook -> $URL"
curl -sS "https://api.telegram.org/bot${TELEGRAM_TOKEN}/setWebhook" \
  --data-urlencode "url=${URL}" \
  --data-urlencode "secret_token=${TELEGRAM_SECRET}" \
  --data-urlencode 'allowed_updates=["message","callback_query"]'
echo
curl -sS "https://api.telegram.org/bot${TELEGRAM_TOKEN}/setMyCommands" -H 'Content-Type: application/json' -d '{"commands":[
  {"command":"start","description":"Subscribe to heat alerts / गर्मी चेतावनी"},
  {"command":"stop","description":"Unsubscribe"},
  {"command":"ask","description":"Officers: ask the plan assistant"}]}'
echo
