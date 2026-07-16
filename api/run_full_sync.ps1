# Full sync state.json -> RDS (existing schema)
# Run in PowerShell if Cursor approval "Run" does not respond.

$ErrorActionPreference = "Stop"
$localApi = "c:\Users\Administrator\Documents\自动点击\current_source\penta5-runner\api"

scp -o BatchMode=yes `
  "$localApi\import_full_existing.js" `
  "$localApi\server.js" `
  penta5:/root/penta5-runner/api/

ssh -o BatchMode=yes penta5 @'
set -e
cd /root/penta5-runner/api
STAMP=$(date +%Y%m%d_%H%M%S)
FREEZE=/root/penta5-runner/data/backups/state_full_sync_${STAMP}.json
cp -f /root/penta5-runner/data/state.json "$FREEZE"
ls -lh "$FREEZE"
node --max-old-space-size=6144 import_full_existing.js --file "$FREEZE"
curl -s http://127.0.0.1:8787/health
echo
pm2 restart penta5-rds-api --update-env
sleep 1
curl -s http://127.0.0.1:8787/health
echo
'@
