"""
Database module - Agente de Afiliados
Schema SQLite com suporte para todas as camadas (1, 2, 3) e ações do usuário.
Conexões são criadas por função (thread-safe) - lição aprendida do Freelance.
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = "/app/data/agent.db"


def get_connection():
    """
    Cria uma nova conexão SQLite.
    IMPORTANTE: sempre criar dentro da função/thread que vai usar.
    Nunca compartilhar conexão entre threads (causou bug no Freelance).
    """
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # melhora concorrência
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Cria tabelas se não existirem."""
    conn = get_connection()
    c = conn.cursor()

    # Tabela principal de produtos
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            
            -- Identificação
            source TEXT NOT NULL,              -- mercadolivre, magalu, amazon, shopee
            external_id TEXT NOT NULL,         -- ID na plataforma
            url TEXT NOT NULL,
            affiliate_url TEXT,                -- URL com ID de afiliado
            
            -- Dados do produto
            title TEXT NOT NULL,
            description TEXT,
            category TEXT,
            category_smart TEXT,               -- categoria normalizada pela Camada 2
            price REAL,
            original_price REAL,               -- preço riscado (se houver desconto)
            discount_percent REAL,
            image_url TEXT,
            
            -- Comissão
            commission_percent REAL,
            commission_value REAL,             -- valor em R$
            
            -- Reputação e vendas
            rating REAL,
            reviews_count INTEGER,
            sales_count INTEGER,               -- quando disponível
            sales_estimate INTEGER,            -- estimativa nossa
            
            -- Vendedor
            seller_name TEXT,
            seller_rating REAL,
            seller_id TEXT,
            is_official_store BOOLEAN DEFAULT 0,
            
            -- Camadas 1 + 2 (Regex)
            match_score REAL,                  -- potencial 0-10
            feasibility_score REAL,            -- viabilidade 0-10
            red_flags TEXT,                    -- JSON array com flags detectadas
            layer2_issues TEXT,                -- JSON array com problemas estruturais
            
            -- Camada 3 (IA)
            ai_status TEXT DEFAULT 'skipped',  -- skipped, pending, done, error
            ai_source TEXT,                    -- groq, gemini
            ai_confidence REAL,                -- 0-10
            ai_verdict TEXT,                   -- 🟢, 🟡, 🟡⭐, 🔴, ⚫
            ai_strategy_hint TEXT,             -- resumo curto da estratégia
            ai_classified_at TIMESTAMP,
            ai_raw_response TEXT,              -- JSON completo da resposta IA
            
            -- Estado do usuário
            analyzed BOOLEAN DEFAULT 0,        -- clicou no botão Claude
            promoted BOOLEAN DEFAULT 0,        -- está promovendo
            ignored BOOLEAN DEFAULT 0,         -- marcou como ignorar
            user_notes TEXT,
            
            -- Timestamps
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_scraped_at TIMESTAMP,
            
            UNIQUE(source, external_id)
        )
    """)

    # Índices para performance
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_source ON products(source)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_match_score ON products(match_score DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_ai_status ON products(ai_status)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_analyzed ON products(analyzed)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_promoted ON products(promoted)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_ignored ON products(ignored)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_products_created ON products(created_at DESC)")

    # Tabela de logs de scraping (auditoria)
    c.execute("""
        CREATE TABLE IF NOT EXISTS scraping_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            finished_at TIMESTAMP,
            products_found INTEGER DEFAULT 0,
            products_new INTEGER DEFAULT 0,
            products_updated INTEGER DEFAULT 0,
            status TEXT,                       -- success, error, partial
            error_message TEXT
        )
    """)

    # Tabela de logs da IA (auditoria)
    c.execute("""
        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            source TEXT,                       -- groq, gemini
            success BOOLEAN,
            latency_ms INTEGER,
            tokens_used INTEGER,
            error_message TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (product_id) REFERENCES products(id)
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Banco de dados inicializado")


def get_stats():
    """Retorna estatísticas gerais do sistema."""
    conn = get_connection()
    c = conn.cursor()

    stats = {}

    c.execute("SELECT COUNT(*) FROM products")
    stats['total'] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM products WHERE match_score >= 6 AND feasibility_score >= 6")
    stats['relevant'] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM products WHERE analyzed = 1")
    stats['analyzed'] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM products WHERE promoted = 1")
    stats['promoted'] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM products WHERE ai_status = 'done'")
    stats['ai_classified'] = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM products WHERE ai_status = 'pending'")
    stats['ai_pending'] = c.fetchone()[0]

    c.execute("SELECT source, COUNT(*) FROM products GROUP BY source")
    stats['by_source'] = {row[0]: row[1] for row in c.fetchall()}

    conn.close()
    return stats


if __name__ == "__main__":
    init_db()
    print(get_stats())
