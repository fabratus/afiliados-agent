#!/usr/bin/env python3
"""
migrate_enrich_status.py
=========================
Migração não-destrutiva: adiciona coluna 'enrich_status' à tabela 'products'.

Segurança:
  - Faz backup do banco ANTES de alterar
  - Usa ADD COLUMN ... DEFAULT 'pending' (operação atômica, não recria tabela)
  - Idempotente: pode rodar N vezes sem efeito colateral

Rodar no VPS:
  cd /home/deploy/afiliados-agent
  python3 scripts/migrate_enrich_status.py
"""

import os
import sys
import sqlite3
import shutil
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH      = os.path.join(PROJECT_ROOT, "data", "agent.db")
BACKUP_DIR   = os.path.join(PROJECT_ROOT, "backups")


def main():
    print("\n" + "=" * 55)
    print("  migrate_enrich_status — Sessão L")
    print("=" * 55 + "\n")

    # ── Verificações pré-migração ──────────────────────────────────
    if not os.path.exists(DB_PATH):
        print(f"❌  Banco não encontrado: {DB_PATH}")
        print("    Verifique se o Docker container foi iniciado ao menos uma vez.")
        sys.exit(1)

    # ── 1. Backup obrigatório ──────────────────────────────────────
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup  = os.path.join(BACKUP_DIR, f"agent_pre_enrich_status_{ts}.db")
    shutil.copy2(DB_PATH, backup)
    print(f"✅  Backup criado: {backup}")
    print(f"    Tamanho: {os.path.getsize(backup):,} bytes\n")

    # ── 2. Inspecionar estado atual ────────────────────────────────
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")  # lição do Freelance

    columns = [
        row[1]
        for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]
    print(f"    Colunas atuais em 'products': {len(columns)}")

    if "enrich_status" in columns:
        print("ℹ️   Coluna 'enrich_status' já existe — nada a fazer.")
        conn.close()
        print("\n✅  Migração: sem alterações necessárias.\n")
        return

    # ── 3. Adicionar coluna ────────────────────────────────────────
    print("    Adicionando coluna 'enrich_status'...")
    conn.execute(
        "ALTER TABLE products ADD COLUMN enrich_status TEXT NOT NULL DEFAULT 'pending'"
    )
    conn.commit()
    print("    ALTER TABLE executado com sucesso.")

    # ── 4. Verificação pós-migração ────────────────────────────────
    columns_after = [
        row[1]
        for row in conn.execute("PRAGMA table_info(products)").fetchall()
    ]
    assert "enrich_status" in columns_after, "Coluna não encontrada após ALTER!"

    # Distribuição dos valores (deve ser tudo 'pending' se banco existia)
    dist = conn.execute(
        "SELECT enrich_status, COUNT(*) FROM products GROUP BY enrich_status"
    ).fetchall()
    conn.close()

    print("\n    Distribuição de enrich_status após migração:")
    for status, count in dist:
        print(f"      {status}: {count} produtos")

    print("\n" + "=" * 55)
    print("  ✅  Migração concluída com sucesso.")
    print(f"     Backup disponível em: {backup}")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    main()
