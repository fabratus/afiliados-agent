#!/bin/bash
# Backup Google Drive via rclone
# Retenção: 30 dias

BACKUP_DIR="$HOME/afiliados-agent/backups"
DB_FILE="$HOME/afiliados-agent/data/agent.db"
DATE=$(date +%Y-%m-%d)
REMOTE_PATH="gdrive:backup-claude/afiliados-agent/$DATE"

if [ ! -f "$DB_FILE" ]; then
    echo "[$(date)] ERRO: banco não encontrado"
    exit 1
fi

# Envia pro Drive
rclone copy "$DB_FILE" "$REMOTE_PATH" --log-level INFO
echo "[$(date)] Backup Drive enviado para $REMOTE_PATH"

# Remove backups antigos do Drive (>30 dias)
CUTOFF_DATE=$(date -d "30 days ago" +%Y-%m-%d)
rclone lsd gdrive:backup-claude/afiliados-agent/ | awk '{print $NF}' | while read dir; do
    if [[ "$dir" < "$CUTOFF_DATE" ]]; then
        rclone purge "gdrive:backup-claude/afiliados-agent/$dir"
        echo "[$(date)] Removido backup antigo: $dir"
    fi
done
