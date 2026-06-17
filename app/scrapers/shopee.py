import os
import time
import hashlib
import logging
import requests

from app.scrapers.base import BaseScraper

logger = logging.getLogger(__name__)

SHOPEE_APP_ID     = os.getenv("SHOPEE_APP_ID", "")
SHOPEE_APP_SECRET = os.getenv("SHOPEE_APP_SECRET", "")
SHOPEE_GRAPHQL_URL = "https://open-api.affiliate.shopee.com.br/graphql"

# Comissões estimadas por categoria Shopee (%)
SHOPEE_COMMISSION_DEFAULT = 5.0
SHOPEE_COMMISSION_BY_CATEGORY = {
    "Electronics": 3.0,
    "Mobile":      3.0,
    "Fashion":     8.0,
    "Beauty":      8.0,
    "Health":      7.0,
    "Sports":      6.0,
    "Home":        6.0,
    "Toys":        6.0,
    "Pet":         7.0,
}

SEARCH_QUERY = """
query searchProducts($keyword: String!, $limit: Int, $page: Int) {
  productOfferV2(keyword: $keyword, limit: $limit, page: $page) {
    nodes {
      itemId
      shopId
      productName
      priceMin
      priceMax
      commissionRate
      sales
      ratingStar
      imageUrl
      productLink
      offerLink
      shopName
      categoryName
    }
    pageInfo {
      hasNextPage
      page
    }
  }
}
"""


def _sign(app_id, timestamp, payload, secret):
    """Gera assinatura SHA256 para autenticação Shopee Affiliate API."""
    message = f"{app_id}{timestamp}{payload}{secret}"
    return hashlib.sha256(message.encode("utf-8")).hexdigest()


class ShopeeScraper(BaseScraper):
    """
    Coleta produtos via Shopee Affiliate GraphQL API (BR).
    Endpoint: https://open-api.affiliate.shopee.com.br/graphql
    Auth: SHA256 Credential=APP_ID,Timestamp=TS,Signature=HASH
    Credenciais: SHOPEE_APP_ID + SHOPEE_APP_SECRET no .env
    """

    source_name = "shopee"

    def __init__(self):
        super().__init__()
        if not SHOPEE_APP_ID or not SHOPEE_APP_SECRET:
            logger.warning("⚠️  SHOPEE_APP_ID / SHOPEE_APP_SECRET não configurados")
        self.session = requests.Session()

    def _auth_headers(self, payload_str):
        timestamp = str(int(time.time()))
        sig = _sign(SHOPEE_APP_ID, timestamp, payload_str, SHOPEE_APP_SECRET)
        return {
            "Content-Type": "application/json",
            "Authorization": f"SHA256 Credential={SHOPEE_APP_ID},Timestamp={timestamp},Signature={sig}",
        }

    def search_products(self, keyword, limit=20, page=1):
        """Busca produtos no Shopee por palavra-chave."""
        import json
        body = json.dumps({
            "query": SEARCH_QUERY,
            "variables": {"keyword": keyword, "limit": limit, "page": page},
        }, separators=(",", ":"))
        headers = self._auth_headers(body)
        try:
            resp = self.session.post(SHOPEE_GRAPHQL_URL, data=body, headers=headers, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            nodes = data.get("data", {}).get("productOfferV2", {}).get("nodes", [])
            logger.info(f"  🛒 Shopee '{keyword}': {len(nodes)} produtos")
            return nodes
        except Exception as e:
            logger.error(f"  ❌ Shopee erro '{keyword}': {e}")
            return []

    def normalize_product(self, raw):
        """Converte produto Shopee para formato padrão do banco."""
        try:
            item_id   = str(raw.get("itemId", ""))
            shop_id   = str(raw.get("shopId", ""))
            external_id = f"{item_id}_{shop_id}"

            price_min = float(raw.get("priceMin", 0) or 0)
            price_max = float(raw.get("priceMax", 0) or 0)
            price     = price_min if price_min > 0 else price_max

            # Filtros básicos
            if price < 15.0 or price > 6000.0:
                return None
            rating = float(raw.get("ratingStar", 0) or 0)
            sales  = int(raw.get("sales", 0) or 0)
            if sales < 5:
                return None

            commission_rate = float(raw.get("commissionRate", 0) or 0)
            if commission_rate == 0:
                commission_rate = SHOPEE_COMMISSION_DEFAULT
            commission_value = round(price * commission_rate / 100, 2)

            category = raw.get("categoryName") or "Geral"

            return {
                "external_id":       external_id,
                "url":               raw.get("productLink"),
                "affiliate_url":     raw.get("offerLink"),
                "title":             (raw.get("productName") or "").strip(),
                "description":       None,
                "category":          category,
                "category_smart":    None,
                "price":             price,
                "original_price":    price_max if price_max > price_min else None,
                "discount_percent":  None,
                "image_url":         raw.get("imageUrl"),
                "commission_percent": commission_rate,
                "commission_value":  commission_value,
                "rating":            rating if rating > 0 else None,
                "reviews_count":     None,
                "sales_count":       sales,
                "sales_estimate":    sales,
                "seller_name":       raw.get("shopName"),
                "seller_rating":     None,
                "seller_id":         shop_id,
                "is_official_store": False,
            }
        except Exception as e:
            logger.error(f"Erro normalizando produto Shopee: {e}")
            return None

    def _get_keywords(self):
        """Combina trends do ML com termos fixos da config."""
        keywords = []
        try:
            from app.scrapers.mercadolivre import MercadoLivreScraper
            ml = MercadoLivreScraper()
            trend_kws = ml.get_trending_keywords()
            keywords.extend(trend_kws[:20])  # top 20 trends ML
            logger.info(f"📈 {len(trend_kws)} trends ML → usando {min(20, len(trend_kws))}")
        except Exception as e:
            logger.warning(f"Trends ML indisponível: {e}")

        try:
            from app.config import ML_SEARCH_TERMS
            for term in ML_SEARCH_TERMS:
                if term not in keywords:
                    keywords.append(term)
        except Exception:
            pass

        return keywords

    def scrape(self):
        """Coleta produtos Shopee usando trends ML + termos fixos."""
        if not SHOPEE_APP_ID or not SHOPEE_APP_SECRET:
            logger.error("❌ Credenciais Shopee não configuradas. Defina SHOPEE_APP_ID e SHOPEE_APP_SECRET no .env")
            return []

        all_products = []
        seen_ids = set()
        keywords = self._get_keywords()

        logger.info(f"🚀 Shopee scraper: {len(keywords)} keywords")
        for kw in keywords:
            if len(all_products) >= 300:
                break
            nodes = self.search_products(kw, limit=20)
            for node in nodes:
                norm = self.normalize_product(node)
                if norm and norm["external_id"] not in seen_ids:
                    seen_ids.add(norm["external_id"])
                    all_products.append(norm)
            time.sleep(1.0)

        logger.info(f"✅ Shopee total: {len(all_products)} produtos")
        return all_products


def run_shopee_scraper():
    return ShopeeScraper().run()
