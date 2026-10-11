# Windows PowerShell: point the Telegram bot at the deployed BotFunction URL.
# Usage:  $env:TELEGRAM_TOKEN="..."; $env:TELEGRAM_SECRET="..."; .\scripts\set_webhook.ps1
param([string]$Stack = "taap", [string]$Region = "ap-south-1")
if (-not $env:TELEGRAM_TOKEN -or -not $env:TELEGRAM_SECRET) { throw "Set TELEGRAM_TOKEN and TELEGRAM_SECRET first" }
$url = aws cloudformation describe-stacks --stack-name $Stack --region $Region `
  --query "Stacks[0].Outputs[?OutputKey=='BotWebhookUrl'].OutputValue" --output text
Write-Host "Webhook -> $url"
$api = "https://api.telegram.org/bot$($env:TELEGRAM_TOKEN)"
Invoke-RestMethod -Method Post -Uri "$api/setWebhook" -ContentType "application/json" -Body (@{
  url = $url; secret_token = $env:TELEGRAM_SECRET; allowed_updates = @("message", "callback_query") } | ConvertTo-Json)
Invoke-RestMethod -Method Post -Uri "$api/setMyCommands" -ContentType "application/json" -Body (@{ commands = @(
  @{ command = "start"; description = "Subscribe to heat alerts" },
  @{ command = "stop"; description = "Unsubscribe" },
  @{ command = "ask"; description = "Officers: ask the plan assistant" }) } | ConvertTo-Json -Depth 4)
