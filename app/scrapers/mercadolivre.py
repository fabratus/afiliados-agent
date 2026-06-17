"""
app/scrapers/mercadolivre.py
=============================
Fonte Mercado Livre — híbrido scraping + API OAuth.

Estratégia (Sessão L):
  Descoberta  → scraping HTML público anônimo (curl-cffi via proxy → Playwright fallback)
  Enriquecimento → API OAuth (/items, /description, /reviews, /users) — só pós-C1
  Link afiliado  → MANUAL (QR code via app ML); build_affiliate_url() retorna None

Partes implementadas por Claude (sensíveis):
  _enrich(), _needs_browser(), build_affiliate_url(), _extract_mlb_id(), _throttle()

Partes a implementar pelo Cursor+Gemini (volume):
  _parse_json_embedded() / _extract_from_json_state() — inspecionar HTML real ML
  _parse_bs4_cards()                                  — ajustar seletores via DevTools
  Dockerfile + requirements.txt                       — adicionar playwright, bs4

Nota: este arquivo substitui o mercadolivre.py anterior (Sessão K).
      A variável ML_PROXY_URL e a lógica de proxy do juca são preservadas
      via BaseHtmlSource.__init__() que lê ML_PROXY_URL do .env.
"""

from __future__ import annotations

import re
import json
import logging
import time
from typing import Optional

import requests as _requests
from bs4 import BeautifulSoup

from app.scrapers.base import BaseHtmlSource, Product

# Token OAuth ML — reusa ml_auth.py da Sessão G (não alterar)
# Se o nome da função for diferente, ajuste aqui:
try:
    from app.ai.ml_auth import get_valid_token as _get_ml_token
except ImportError:
    logger_tmp = logging.getLogger(__name__)
    logger_tmp.warning("ml_auth.get_valid_token não encontrado — enriquecimento desativado")
    _get_ml_token = None  # type: ignore

logger = logging.getLogger(__name__)

ML_API_BASE    = "https://api.mercadolibre.com"
ML_SEARCH_BASE = "https://lista.mercadolivre.com.br"

# Mapeamento de nível de reputação ML para float 1.0-5.0
_ML_LEVEL_MAP: dict[str, float] = {
    "5_green":       5.0,
    "4_light_green": 4.0,
    "3_yellow":      3.0,
    "2_orange_red":  2.0,
    "1_red":         1.0,
}


class MercadoLivreSource(BaseHtmlSource):
    """
    Fonte Mercado Livre.
    Herda BaseHtmlSource (proxy via juca, circuit breaker, Playwright fallback).
    """

    name = "mercadolivre"

    # Throttle levemente maior que o padrão — ML é sensível a burst
    DEFAULT_THROTTLE_S = 2.8

    # ══════════════════════════════════════════════════════════════════════
    #  SEARCH — Descoberta via scraping HTML público
    # ══════════════════════════════════════════════════════════════════════

    def search(self, query: str, filters: dict) -> list[Product]:
        """
        Busca produtos via scraping da página de lista ML.
        Retorna produtos com enrich_status='pending' (enriquecimento roda depois, pós-C1).

        Args:
            query:   termo de busca, ex: "fone bluetooth"
            filters: dict com chaves opcionais:
                     max_pages (int, padrão 3)
                     min_price (float)
                     max_price (float)

        Returns:
            Lista de Product normalizados.
        """
        products: list[Product] = []
        max_pages = int(filters.get("max_pages", 3))

        # ML slug: espaços → hífens
        slug = re.sub(r"\s+", "-", query.strip().lower())
        slug = re.sub(r"[^a-z0-9\-]", "", slug)

        for page in range(1, max_pages + 1):
            # Paginação ML: page 1 sem sufixo; page N com _Desde_{(N-1)*48+1}
            if page == 1:
                url = f"{ML_SEARCH_BASE}/{slug}"
            else:
                desde = (page - 1) * 48 + 1
                url = f"{ML_SEARCH_BASE}/{slug}_Desde_{desde}"

            logger.info(f"[ML] search p{page} → {url}")
            html = self._fetch_robust(url)

            if not html:
                logger.warning(f"[ML] Página {page} retornou vazio/None — parando")
                break

            page_products = self._parse_search_html(html, query)

            if not page_products:
                logger.info(f"[ML] Sem produtos na página {page} — parando paginação")
                break

            # Filtro de preço (se fornecido)
            min_p = filters.get("min_price")
            max_p = filters.get("max_price")
            if min_p or max_p:
                page_products = [
                    p for p in page_products
                    if (min_p is None or p.price >= min_p)
                    and (max_p is None or p.price <= max_p)
                ]

            products.extend(page_products)
            logger.info(
                f"[ML] p{page}: {len(page_products)} produtos "
                f"(acumulado: {len(products)})"
            )

            if page < max_pages:
                self._throttle()

        return products

    # ── Parsing: JSON embutido primeiro, BS4 como fallback ────────────────

    def _parse_search_html(self, html: str, query: str) -> list[Product]:
        """Tenta JSON embutido; se não encontrar, usa BeautifulSoup."""
        products = self._parse_json_embedded(html, query)
        if products:
            return products
        return self._parse_bs4_cards(html, query)

    def _parse_json_embedded(self, html: str, query: str) -> list[Product]:
        """
        Tenta extrair dados estruturados do JSON embutido na página ML.
        ML usa frameworks próprios — os patterns podem mudar com deploys.

        → CURSOR: inspecionar o HTML real de lista.mercadolivre.com.br
          e atualizar os patterns conforme o que estiver na <script> atual.
          Logar os primeiros 3000 chars de html se nenhum pattern bater.
        """
        # Patterns conhecidos (mais específico primeiro)
        patterns = [
            r'window\.__PRELOADED_STATE__\s*=\s*({.+?});\s*(?:</script>|window\.)',
            r'window\.__STORES__\s*=\s*({.+?});\s*(?:</script>|window\.)',
            r'window\.initialState\s*=\s*({.+?});\s*</script>',
            r'<script[^>]+type=["\']application/json["\'][^>]*>\s*({[^<]{100,}})\s*</script>',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.DOTALL | re.IGNORECASE)
            if match:
                try:
                    data = json.loads(match.group(1))
                    products = self._extract_from_json_state(data, query)
                    if products:
                        logger.debug(f"[ML] JSON embutido: {len(products)} produtos")
                        return products
                except (json.JSONDecodeError, KeyError) as e:
                    logger.debug(f"[ML] JSON parse falhou ({pattern[:40]}…): {e}")

        # Sem JSON encontrado — logar para diagnóstico
        logger.info(
            "[ML] Nenhum JSON embutido encontrado — fallback para BS4. "
            f"Primeiros 200 chars: {html[:200]!r}"
        )
        return []

    def _extract_from_json_state(self, data: dict, query: str) -> list[Product]:
        """
        Extrai produtos de um dict JSON de estado da página ML.

        → CURSOR: implementar após inspecionar a estrutura real de 'data'.
          Caminhos típicos a tentar:
            data.get('results')
            data.get('initialState', {}).get('results')
            data.get('search', {}).get('results')
            data.get('items')
          Para cada item bruto, criar Product com pelo menos:
            source, external_id (MLB_ID), url, title, price, image_url
          Usar _extract_mlb_id(url) para obter external_id.
        """
        # Placeholder — retorna vazio até Cursor implementar
        return []

    def _parse_bs4_cards(self, html: str, query: str) -> list[Product]:
        """
        Fallback: BeautifulSoup nos cards do grid de busca.

        → CURSOR: abrir lista.mercadolivre.com.br/{qualquer-busca} no Chrome,
          inspecionar DevTools e confirmar/atualizar os seletores abaixo.
          ML usa design system Andes — nomes de classe mudam ocasionalmente.

        Seletores verificados em meados de 2025 (podem ter mudado):
          Container: .ui-search-results__item
          Título:    .poly-component__title  ou  .ui-search-item__title
          Preço:     .poly-price__current .andes-money-amount__fraction
          Link:      a[href] dentro do card (primeiro <a> com href de produto)
          Imagem:    img[data-src] ou img[src] dentro do card
        """
        soup     = BeautifulSoup(html, "html.parser")
        products = []

        # → CURSOR: ajustar seletor de container se necessário
        cards = soup.select(
            ".ui-search-results__item, "
            ".andes-card.poly-card, "
            "[data-testid='polycard-container']"
        )

        if not cards:
            logger.warning(
                "[ML] BS4: nenhum card encontrado. "
                f"Verificar seletores. len(html)={len(html)}"
            )
            return []

        for card in cards:
            try:
                # Título
                title_el = card.select_one(
                    ".poly-component__title, "
                    ".ui-search-item__title, "
                    "h2.poly-box"
                )
                # Preço (fração inteira)
                price_el = card.select_one(
                    ".poly-price__current .andes-money-amount__fraction, "
                    ".price-tag-fraction, "
                    ".andes-money-amount__fraction"
                )
                # Link do produto
                link_el = card.select_one(
                    "a[href*='mercadolivre.com.br'], "
                    "a.poly-component__title"
                )
                # Imagem
                img_el = card.select_one("img[data-src], img[src]")

                if not (title_el and price_el and link_el):
                    continue

                href = link_el.get("href", "")
                # Limpar parâmetros de tracking da URL
                url = href.split("#")[0].split("?")[0].strip()
                if not url:
                    continue

                mlb_id = self._extract_mlb_id(url)
                if not mlb_id:
                    logger.debug(f"[ML] Sem MLB_ID em url={url[:60]}")
                    continue

                # Preço: remover separadores de milhar BR (.) e converter vírgula
                price_raw = price_el.get_text(strip=True)
                price_str = re.sub(r"[^\d,]", "", price_raw).replace(",", ".")
                if not price_str:
                    continue
                price = float(price_str)

                # Centavos (opcional — card pode ter .andes-money-amount__cents)
                cents_el = card.select_one(".andes-money-amount__cents")
                if cents_el:
                    try:
                        cents = int(cents_el.get_text(strip=True).replace(",", ""))
                        price += cents / 100
                    except ValueError:
                        pass

                image_url = None
                if img_el:
                    image_url = img_el.get("data-src") or img_el.get("src")
                    # Remover placeholder base64 do lazy load
                    if image_url and image_url.startswith("data:"):
                        image_url = None

                products.append(Product(
                    source=self.name,
                    external_id=mlb_id,
                    url=url,
                    title=title_el.get_text(strip=True),
                    price=price,
                    image_url=image_url,
                    enrich_status="pending",
                ))

            except Exception as e:
                logger.debug(f"[ML] BS4 erro ao parsear card: {e}")
                continue

        logger.debug(f"[ML] BS4: {len(products)} produtos de {len(cards)} cards")
        return products

    # ══════════════════════════════════════════════════════════════════════
    #  ANTI-BOT — Override de _needs_browser para sinais específicos ML
    # ══════════════════════════════════════════════════════════════════════

    def _needs_browser(self, html: Optional[str]) -> bool:
        """
        Sinais de bloqueio específicos do ML, além dos genéricos do base.
        Detecta: página de ads em vez de resultados, results vazio, banimento.
        """
        # Verificações base (challenge genérico, vazio, etc.)
        if super()._needs_browser(html):
            return True

        # Sinais ML específicos
        ml_signals = (
            "você foi bloqueado",
            "you have been blocked",
            # Página de resultado vazia (JSON state com lista vazia)
            '"results":[]',
            '"results": []',
            # ML redireciona para ads quando detecta bot
            "meli-dynamic-ads",
            # Página de manutenção
            "em breve estaremos de volta",
        )
        html_lower = html.lower()
        for signal in ml_signals:
            if signal in html_lower:
                logger.info(f"[ML] Sinal de bloqueio detectado: '{signal[:40]}'")
                return True

        return False

    # ══════════════════════════════════════════════════════════════════════
    #  ENRIQUECIMENTO — API OAuth (só roda pós-C1, nos aprovados)
    # ══════════════════════════════════════════════════════════════════════

    def _get_api_token(self) -> Optional[str]:
        """Obtém token OAuth ML válido com auto-refresh (ml_auth.py Sessão G)."""
        if _get_ml_token is None:
            return None
        try:
            return _get_ml_token()
        except Exception as e:
            logger.error(f"[ML] Falha ao obter token OAuth: {e}")
            return None

    def _api_get(self, path: str, token: str) -> Optional[dict]:
        """
        GET autenticado para ML API.
        NÃO usa proxy (chamada OAuth vai direto do VPS para ML API).
        """
        try:
            r = _requests.get(
                f"{ML_API_BASE}{path}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            if r.status_code == 200:
                return r.json()
            logger.warning(f"[ML API] {path} → HTTP {r.status_code}")
            return None
        except Exception as e:
            logger.error(f"[ML API] {path} → exceção: {e}")
            return None

    def enrich(self, product: Product) -> Product:
        """
        Enriquece um produto com dados da API OAuth ML.
        Método público — chamado pelo orquestrador pós-C1.

        Endpoints usados (confirmar com Passo 0):
          /items/{id}             → sold_quantity, condition, seller_id, is_official_store
          /items/{id}/description → plain_text description
          /reviews/item/{id}      → rating_average, total reviews
          /users/{seller_id}      → seller nickname + reputation level

        Degradação graciosa:
          Cada endpoint é independente. Falha em um não cancela os outros.
          enrich_status = 'enriched'     se ≥ 1 endpoint respondeu 200
          enrich_status = 'enrich_failed' se todos falharam
        """
        mlb_id = product.external_id
        token  = self._get_api_token()

        if not token:
            product.enrich_status = "enrich_failed"
            logger.warning(f"[ML] enrich {mlb_id}: sem token OAuth — skip")
            return product

        enriched_any = False

        # ── 1. Detalhe do item ────────────────────────────────────────
        item_data = self._api_get(f"/items/{mlb_id}", token)
        if item_data:
            product.sales_count      = item_data.get("sold_quantity")
            product.seller_id        = str(item_data.get("seller_id") or "")
            product.is_official_store = item_data.get("official_store_id") is not None

            # Preço confirmado pela API (mais confiável que scraping)
            api_price = item_data.get("price")
            if api_price is not None:
                product.price = float(api_price)

            # Preço original (para cálculo de desconto)
            original_price = item_data.get("original_price")
            if original_price is not None:
                product.original_price = float(original_price)
                if product.original_price > 0:
                    product.discount_percent = round(
                        (1 - product.price / product.original_price) * 100, 1
                    )

            # Categoria ML
            product.category = item_data.get("category_id")

            enriched_any = True
            logger.debug(f"[ML] /items/{mlb_id} OK — seller_id={product.seller_id}")

        # ── 2. Descrição ────────────────────────────────────────────
        desc_data = self._api_get(f"/items/{mlb_id}/description", token)
        if desc_data:
            text = desc_data.get("plain_text") or desc_data.get("text") or ""
            product.description = text[:2000] if text else None  # limitar tamanho
            enriched_any = True

        # ── 3. Reviews ──────────────────────────────────────────────
        reviews_data = self._api_get(f"/reviews/item/{mlb_id}", token)
        if reviews_data:
            product.rating        = reviews_data.get("rating_average")
            paging                = reviews_data.get("paging", {})
            product.reviews_count = paging.get("total")
            enriched_any = True

        # ── 4. Reputação do vendedor ─────────────────────────────────
        if product.seller_id:
            user_data = self._api_get(f"/users/{product.seller_id}", token)
            if user_data:
                # Nome do vendedor
                product.seller_name = user_data.get("nickname") or product.seller_name

                # Reputação: level_id → float 1.0-5.0
                rep        = user_data.get("seller_reputation", {})
                level_id   = rep.get("level_id", "")
                product.seller_rating = _ML_LEVEL_MAP.get(level_id)

                # Alternativa mais granular: % positivo
                # transactions = rep.get("transactions", {})
                # positive_pct = transactions.get("ratings", {}).get("positive")
                # if positive_pct is not None:
                #     product.seller_rating = round(positive_pct / 20, 2)  # 0-100 → 0-5

                enriched_any = True
        else:
            logger.debug(f"[ML] seller_id ausente para {mlb_id} — pulando /users")

        # ── Status final ─────────────────────────────────────────────
        product.enrich_status = "enriched" if enriched_any else "enrich_failed"
        logger.info(
            f"[ML] enrich {mlb_id} → {product.enrich_status} "
            f"(price={product.price}, sales={product.sales_count}, "
            f"rating={product.rating})"
        )
        return product

    # ══════════════════════════════════════════════════════════════════════
    #  AFILIADO — Link manual (QR code via app ML)
    # ══════════════════════════════════════════════════════════════════════

    def build_affiliate_url(self, product: Product) -> Optional[str]:
        """
        ML exige geração de link via app mobile (QR code).
        Retorna None — link será preenchido manualmente pelo usuário ao promover.
        """
        return None

    # ══════════════════════════════════════════════════════════════════════
    #  UTILITÁRIOS
    # ══════════════════════════════════════════════════════════════════════

    def _extract_mlb_id(self, url: str) -> Optional[str]:
        """
        Extrai o MLB ID de qualquer formato de URL do Mercado Livre.

        Formatos conhecidos:
          .../p/MLB23847609             (página de produto)
          .../MLB-3045123456-titulo-...  (URL de item)
          .../MLB_3045123456            (variante com underscore)
          MLB2654328961                 (ID direto no path)
        """
        match = re.search(r"MLB[-_]?(\d{8,12})", url, re.IGNORECASE)
        if match:
            return f"MLB{match.group(1)}"
        return None

    def _throttle(self, seconds: Optional[float] = None) -> None:
        """Throttle ML: levemente acima do padrão para evitar rate-limit."""
        time.sleep(seconds if seconds is not None else self.DEFAULT_THROTTLE_S)
