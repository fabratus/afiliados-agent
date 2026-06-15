"""
Scraper Mercado Livre - API oficial pública
Documentação: https://developers.mercadolibre.com/pt_br/itens-e-buscas
"""

import requests
import time
import logging
from app.scrapers.base import BaseScraper
from app.config import (
    ML_SEARCH_TERMS,
    ML_CATEGORIES,
    ML_PRODUCTS_PER_TERM,
    ML_PRODUCTS_PER_CATEGORY,
    ML_REQUEST_DELAY,
    ML_MAX_PRODUCTS_PER_RUN,
    ML_MIN_PRICE,
    ML_MAX_PRICE,
    ML_MIN_REVIEWS,
    ML_MIN_SOLD,
    ML_COMMISSION_BY_CATEGORY,
)

logger = logging.getLogger(__name__)


class MercadoLivreScraper(BaseScraper):
    """
    Coleta produtos via API pública do Mercado Livre.
    Endpoint: https://api.mercadolibre.com/sites/MLB/search
    """
    
    source_name = "mercadolivre"
    BASE_URL = "https://api.mercadolibre.com"
    
    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "AfiliadosAgent/1.0",
            "Accept": "application/json"
        })
        self.collected_ids = set()  # evita duplicatas dentro da mesma rodada
    
    def search_by_term(self, term, limit=20):
        """Busca produtos por palavra-chave."""
        url = f"{self.BASE_URL}/sites/MLB/search"
        params = {
            "q": term,
            "limit": limit,
            "sort": "sold_quantity_desc"  # mais vendidos primeiro
        }
        
        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            logger.info(f"  🔍 Termo '{term}': {len(results)} produtos")
            return results
        except Exception as e:
            logger.error(f"  ❌ Erro buscando '{term}': {e}")
            return []
    
    def search_by_category(self, category_id, category_name, limit=30):
        """Busca produtos por categoria."""
        url = f"{self.BASE_URL}/sites/MLB/search"
        params = {
            "category": category_id,
            "limit": limit,
            "sort": "sold_quantity_desc"
        }
        
        try:
            resp = self.session.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            logger.info(f"  📂 Categoria '{category_name}': {len(results)} produtos")
            return results
        except Exception as e:
            logger.error(f"  ❌ Erro categoria '{category_name}': {e}")
            return []
    
    def normalize_product(self, raw):
        """
        Transforma produto da API em dict normalizado para o banco.
        Retorna None se produto não passa nos filtros básicos.
        """
        try:
            # Filtros básicos de descarte
            price = raw.get("price", 0)
            if price < ML_MIN_PRICE or price > ML_MAX_PRICE:
                return None
            
            sold = raw.get("sold_quantity", 0)
            if sold < ML_MIN_SOLD:
                return None
            
            # Dados principais
            external_id = raw.get("id")
            if not external_id or external_id in self.collected_ids:
                return None
            self.collected_ids.add(external_id)
            
            # Preço original (se tiver desconto)
            original_price = raw.get("original_price")
            discount_percent = None
            if original_price and original_price > price:
                discount_percent = round(((original_price - price) / original_price) * 100, 1)
            
            # Categoria + comissão estimada
            category_id = raw.get("category_id", "")
            commission_percent = ML_COMMISSION_BY_CATEGORY.get(
                category_id,
                ML_COMMISSION_BY_CATEGORY["default"]
            )
            commission_value = round(price * commission_percent / 100, 2)
            
            # Vendedor
            seller = raw.get("seller", {})
            seller_id = str(seller.get("id", "")) if seller else None
            is_official = raw.get("official_store_id") is not None
            
            # Rating (quando disponível)
            reviews_data = raw.get("reviews") or {}
            rating = reviews_data.get("rating_average")
            reviews_count = reviews_data.get("total", 0)
            
            # Filtro adicional: mínimo de reviews (se tiver dados)
            if reviews_count > 0 and reviews_count < ML_MIN_REVIEWS:
                return None
            
            return {
                "external_id": external_id,
                "url": raw.get("permalink"),
                "affiliate_url": None,  # ML não permite automação - gerado manualmente
                "title": raw.get("title", "").strip(),
                "description": None,  # API search não retorna descrição
                "category": category_id,
                "category_smart": None,  # preenchido pela Camada 2 depois
                "price": price,
                "original_price": original_price,
                "discount_percent": discount_percent,
                "image_url": raw.get("thumbnail"),
                "commission_percent": commission_percent,
                "commission_value": commission_value,
                "rating": rating,
                "reviews_count": reviews_count,
                "sales_count": sold,
                "sales_estimate": sold,  # ML retorna valor exato
                "seller_name": None,  # buscado à parte se precisar
                "seller_rating": None,
                "seller_id": seller_id,
                "is_official_store": is_official,
            }
        except Exception as e:
            logger.error(f"Erro normalizando produto: {e}")
            return None
    
    def scrape(self):
        """Executa a coleta completa."""
        all_products = []
        total_limit = ML_MAX_PRODUCTS_PER_RUN
        
        # Fase 1: busca por termos
        logger.info("📍 Fase 1: Busca por termos")
        for term in ML_SEARCH_TERMS:
            if len(all_products) >= total_limit:
                break
            
            raw_results = self.search_by_term(term, ML_PRODUCTS_PER_TERM)
            for raw in raw_results:
                normalized = self.normalize_product(raw)
                if normalized:
                    all_products.append(normalized)
                if len(all_products) >= total_limit:
                    break
            
            time.sleep(ML_REQUEST_DELAY)
        
        # Fase 2: busca por categorias
        logger.info("📍 Fase 2: Busca por categorias")
        for cat_id, cat_name in ML_CATEGORIES:
            if len(all_products) >= total_limit:
                break
            
            raw_results = self.search_by_category(cat_id, cat_name, ML_PRODUCTS_PER_CATEGORY)
            for raw in raw_results:
                normalized = self.normalize_product(raw)
                if normalized:
                    all_products.append(normalized)
                if len(all_products) >= total_limit:
                    break
            
            time.sleep(ML_REQUEST_DELAY)
        
        logger.info(f"✅ Total coletado: {len(all_products)} produtos")
        return all_products


def run_mercadolivre_scraper():
    """Função helper para executar o scraper."""
    scraper = MercadoLivreScraper()
    return scraper.run()
