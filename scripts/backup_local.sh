#!/bin/bash
# Backup local do Agente de Afiliados
# Retenção: 7 dias

BACKUP_DIR="$HOME/afiliados-agent/backups"
DB_FILE="$HOME/afiliados-agent/data/agent.db"
DATE=$(date +%Y-%m-%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

# Copia o banco
if [ -f "$DB_FILE" ]; then
    cp "$DB_FILE" "$BACKUP_DIR/agent_${DATE}.db"
    echo "[$(date)] Backup local criado: agent_${DATE}.db"
else
    echo "[$(date)] ERRO: banco não encontrado em $DB_FILE"
    exit 1
fi

# Remove backups com mais de 7 dias
find "$BACKUP_DIR" -name "agent_*.db" -mtime +7 -delete

echo "[$(date)] Backup local finalizado"
