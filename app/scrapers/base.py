"""
Base Scraper - Classe abstrata reutilizável
Todos os scrapers (ML, Magalu, Amazon, Shopee) herdam dessa base
"""

import time
import logging
from datetime import datetime
from app.database import get_connection

logger = logging.getLogger(__name__)


class BaseScraper:
    """
    Base abstrata para scrapers.
    Cada scraper filho implementa:
    - source_name (string identificadora)
    - scrape() → retorna lista de dicts com produtos normalizados
    """
    
    source_name = "base"  # override no filho
    
    def __init__(self):
        self.stats = {
            "found": 0,
            "new": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0
        }
        self.log_id = None
    
    def start_log(self):
        """Cria entrada em scraping_logs e retorna o ID."""
        conn = get_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO scraping_logs (source, started_at) VALUES (?, ?)",
            (self.source_name, datetime.now())
        )
        self.log_id = c.lastrowid
        conn.commit()
        conn.close()
        return self.log_id
    
    def finish_log(self, status="success", error_message=None):
        """Finaliza o log com stats finais."""
        conn = get_connection()
        c = conn.cursor()
        c.execute("""
            UPDATE scraping_logs
            SET finished_at = ?,
                products_found = ?,
                products_new = ?,
                products_updated = ?,
                status = ?,
                error_message = ?
            WHERE id = ?
        """, (
            datetime.now(),
            self.stats["found"],
            self.stats["new"],
            self.stats["updated"],
            status,
            error_message,
            self.log_id
        ))
        conn.commit()
        conn.close()
    
    def upsert_product(self, product_data):
        """
        Insere ou atualiza produto no banco.
        Se existe (source + external_id): atualiza dados dinâmicos.
        Se não existe: insere novo.
        
        Retorna 'new' ou 'updated' ou 'error'.
        """
        conn = get_connection()
        c = conn.cursor()
        
        try:
            # Verifica se produto já existe
            c.execute("""
                SELECT id FROM products
                WHERE source = ? AND external_id = ?
            """, (self.source_name, product_data["external_id"]))
            
            existing = c.fetchone()
            
            if existing:
                # UPDATE - atualiza apenas dados que mudam
                c.execute("""
                    UPDATE products SET
                        title = ?,
                        price = ?,
                        original_price = ?,
                        discount_percent = ?,
                        rating = ?,
                        reviews_count = ?,
                        sales_count = ?,
                        sales_estimate = ?,
                        seller_rating = ?,
                        is_official_store = ?,
                        image_url = ?,
                        updated_at = ?,
                        last_scraped_at = ?
                    WHERE id = ?
                """, (
                    product_data.get("title"),
                    product_data.get("price"),
                    product_data.get("original_price"),
                    product_data.get("discount_percent"),
                    product_data.get("rating"),
                    product_data.get("reviews_count"),
                    product_data.get("sales_count"),
                    product_data.get("sales_estimate"),
                    product_data.get("seller_rating"),
                    product_data.get("is_official_store", False),
                    product_data.get("image_url"),
                    datetime.now(),
                    datetime.now(),
                    existing["id"]
                ))
                conn.commit()
                self.stats["updated"] += 1
                return "updated"
            else:
                # INSERT - produto novo
                c.execute("""
                    INSERT INTO products (
                        source, external_id, url, affiliate_url,
                        title, description, category, category_smart,
                        price, original_price, discount_percent, image_url,
                        commission_percent, commission_value,
                        rating, reviews_count, sales_count, sales_estimate,
                        seller_name, seller_rating, seller_id, is_official_store,
                        last_scraped_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    self.source_name,
                    product_data["external_id"],
                    product_data["url"],
                    product_data.get("affiliate_url"),
                    product_data["title"],
                    product_data.get("description"),
                    product_data.get("category"),
                    product_data.get("category_smart"),
                    product_data.get("price"),
                    product_data.get("original_price"),
                    product_data.get("discount_percent"),
                    product_data.get("image_url"),
                    product_data.get("commission_percent"),
                    product_data.get("commission_value"),
                    product_data.get("rating"),
                    product_data.get("reviews_count"),
                    product_data.get("sales_count"),
                    product_data.get("sales_estimate"),
                    product_data.get("seller_name"),
                    product_data.get("seller_rating"),
                    product_data.get("seller_id"),
                    product_data.get("is_official_store", False),
                    datetime.now()
                ))
                conn.commit()
                self.stats["new"] += 1
                return "new"
                
        except Exception as e:
            logger.error(f"Erro ao upsert produto {product_data.get('external_id')}: {e}")
            self.stats["errors"] += 1
            return "error"
        finally:
            conn.close()
    
    def run(self):
        """Executa o scraper completo - template method."""
        logger.info(f"🚀 Iniciando scraper: {self.source_name}")
        self.start_log()
        
        try:
            products = self.scrape()
            self.stats["found"] = len(products)
            
            for product in products:
                self.upsert_product(product)
            
            self.finish_log(status="success")
            logger.info(f"✅ Scraper {self.source_name} finalizado: {self.stats}")
            return self.stats
            
        except Exception as e:
            logger.error(f"❌ Scraper {self.source_name} falhou: {e}")
            self.finish_log(status="error", error_message=str(e))
            raise
    
    def scrape(self):
        """Implementar no filho - retorna lista de produtos normalizados."""
        raise NotImplementedError("Subclasse deve implementar scrape()")
