"""
app/scrapers/base.py
====================
Contrato base para todas as fontes de produtos do Afiliados Agent.

Hierarquia:
    BaseSource (ABC)
    ├── BaseApiSource   → Shopee, AliExpress, Awin, Lomadee
    └── BaseHtmlSource  → MercadoLivre, Magalu, Amazon

Design aprovado na Sessão L. Implementação por Claude (arquitetura crítica).
Não alterar sem revisão — este arquivo define o contrato que todos os scrapers seguem.
"""

from __future__ import annotations

import os
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
#  PRODUCT — Objeto de transferência entre fontes e o funil
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class Product:
    """
    Objeto normalizado que flui por todo o funil:
        fonte → C1 (regex) → C2 (regex) → C3 (IA) → C4 (Claude)

    Campos obrigatórios: source, external_id, url, title, price.
    Todo o restante é Optional — cada camada preenche o que consegue.

    Mapeamento com tabela 'products' do banco:
        Campos de scraper → inseridos na criação (INSERT ... ON CONFLICT)
        Campos de analyzer.py → atualizados pela C1/C2
        Campos de ai_classifier.py → atualizados pela C3
        Campos de timestamps → gerenciados pelo banco (DEFAULT, trigger)
    """

    # ── Identificação (obrigatório) ───────────────────────────────────────
    source:      str   # "mercadolivre" | "aliexpress" | "awin" | …
    external_id: str   # ID único na plataforma  ex: "MLB3045123456"
    url:         str   # URL canônica do produto (sem parâmetros de tracking)
    title:       str
    price:       float

    # ── Produto ──────────────────────────────────────────────────────────
    image_url:        Optional[str]   = None
    original_price:   Optional[float] = None
    discount_percent: Optional[float] = None
    description:      Optional[str]   = None
    category:         Optional[str]   = None

    # ── Comissão / afiliado ───────────────────────────────────────────────
    commission_percent: Optional[float] = None
    commission_value:   Optional[float] = None
    affiliate_url:      Optional[str]   = None  # None = link manual (ML via QR code)

    # ── Métricas do produto ───────────────────────────────────────────────
    rating:         Optional[float] = None
    reviews_count:  Optional[int]   = None
    sales_count:    Optional[int]   = None
    sales_estimate: Optional[int]   = None

    # ── Vendedor ─────────────────────────────────────────────────────────
    seller_name:               Optional[str]   = None
    seller_id:                 Optional[str]   = None
    seller_rating:             Optional[float] = None
    is_official_store:         bool            = False
    seller_reputation_external: Optional[str]  = None  # ReclameAqui — V1.5

    # ── Enriquecimento ────────────────────────────────────────────────────
    enrich_status: str = "pending"
    # pending       → scraped, enriquecimento ainda não executado
    # enriched      → API chamada com sucesso, campos extras preenchidos
    # scraping_only → fonte sem API de enriquecimento (ex: só HTML)
    # enrich_failed → API chamada mas falhou (403, timeout, etc.)

    # ── Camadas 1+2 — preenchido por analyzer.py ─────────────────────────
    match_score:       Optional[float] = None
    feasibility_score: Optional[float] = None
    red_flags:         list = field(default_factory=list)  # JSON em banco
    layer2_issues:     list = field(default_factory=list)  # JSON em banco
    category_smart:    Optional[str]   = None

    # ── Camada 3 / IA — preenchido por ai_classifier.py ──────────────────
    ai_status:        Optional[str]   = None
    ai_source:        Optional[str]   = None
    ai_confidence:    Optional[float] = None
    ai_verdict:       Optional[str]   = None
    ai_strategy_hint: Optional[str]   = None
    ai_raw_response:  Optional[str]   = None

    # ── Features ricas — preenchido por enrichment/ (V1.5) ───────────────
    roi_estimate:     Optional[float] = None
    saturation_level: Optional[str]   = None   # "low" | "medium" | "high"
    funnel_keywords:  list = field(default_factory=list)  # JSON em banco
    meta_ads_count:   Optional[int]   = None
    niche:            Optional[str]   = None
    niche_confidence: Optional[float] = None

    # ── Estado do usuário ────────────────────────────────────────────────
    analyzed:   bool         = False
    promoted:   bool         = False
    ignored:    bool         = False
    user_notes: Optional[str] = None
    # created_at, updated_at, last_scraped_at → gerenciados pelo banco


# ══════════════════════════════════════════════════════════════════════════════
#  BASE SOURCE — Contrato mínimo para toda fonte
# ══════════════════════════════════════════════════════════════════════════════

class BaseSource(ABC):
    """
    Contrato que toda fonte deve implementar.
    O funil (orquestrador) sempre recebe list[Product] normalizado,
    indiferente a se a origem é API ou scraping.
    """

    name: str  # identificador único  ex: "mercadolivre", "aliexpress"

    @abstractmethod
    def search(self, query: str, filters: dict) -> list[Product]:
        """
        Busca produtos e retorna lista normalizada.
        Cada produto sai com enrich_status='pending'.
        O funil decide quais enriquecer após a C1.
        """
        ...

    @abstractmethod
    def build_affiliate_url(self, product: Product) -> Optional[str]:
        """
        Gera URL de afiliado rastreada para o produto.
        Retorna None quando o link é gerado manualmente (ML via QR code).
        """
        ...


# ══════════════════════════════════════════════════════════════════════════════
#  BASE API SOURCE — Fontes com API oficial autenticada
#  Subclasses concretas: aliexpress.py, awin.py, shopee.py, lomadee.py
# ══════════════════════════════════════════════════════════════════════════════

class BaseApiSource(BaseSource, ABC):
    """
    Esqueleto para fontes com API.
    Subclasses implementam _signed_request() e _json_to_product()
    conforme o schema da API de cada plataforma.
    """

    def _signed_request(self, endpoint: str, params: dict) -> Optional[dict]:
        """
        Placeholder para assinaturas. Cada subclasse implementa o próprio:
        - AliExpress/Shopee: SHA256 com app_key + timestamp + sorted params
        - Awin: Bearer token simples
        - Lomadee: token de publisher no header
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} deve implementar _signed_request()"
        )

    def _json_to_product(self, raw: dict) -> Optional[Product]:
        """
        Converte um item bruto da API em Product normalizado.
        Cada subclasse mapeia os campos conforme o schema da plataforma.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} deve implementar _json_to_product()"
        )


# ══════════════════════════════════════════════════════════════════════════════
#  BASE HTML SOURCE — Fontes que requerem scraping
#  Subclasses concretas: mercadolivre.py, magalu.py, amazon.py
# ══════════════════════════════════════════════════════════════════════════════

class BaseHtmlSource(BaseSource, ABC):
    """
    Base para fontes scraping.

    Provê:
    - _fetch()           : GET via proxy residencial (juca), requests + circuit breaker
    - _needs_browser()   : detecta challenge/vazio (override por subclasse)
    - _fetch_with_browser(): escalação para Playwright headless (1 por vez)
    - _fetch_robust()    : método que scrapers devem chamar (híbrido curl→Playwright)
    - _throttle()        : pausa configurável entre requisições

    Arquitetura de proxy:
        Container VPS → localhost:9091 (SSH tunnel) → juca:9090 (curl-cffi proxy)
        Scraping: via proxy  (IP residencial juca + TLS impersonation)
        Playwright: sem proxy (IP VPS diretamente — aceitável como fallback raro)
        OAuth API: sem proxy  (VPS autenticado — não passa pelo juca)

    Circuit breaker:
        Idêntico ao padrão do Freelance Agent:
        5 falhas consecutivas → pausa de 90s, reset do contador.
    """

    # ── Parâmetros (subclasses podem sobrescrever) ────────────────────────
    CIRCUIT_BREAKER_THRESHOLD: int   = 5     # falhas antes de abrir
    CIRCUIT_BREAKER_PAUSE_S:   int   = 90    # segundos de pausa
    DEFAULT_THROTTLE_S:        float = 2.5   # segundos entre páginas

    def __init__(self) -> None:
        self._proxy_url = os.getenv("ML_PROXY_URL")  # ex: http://localhost:9091
        self._consecutive_failures: int   = 0
        self._circuit_open_until:   float = 0.0
        self._session = self._build_session()

    # ── Sessão requests (scraping via proxy) ──────────────────────────────

    def _build_session(self):
        import requests
        session = requests.Session()
        if self._proxy_url:
            session.proxies = {
                "http":  self._proxy_url,
                "https": self._proxy_url,
            }
        session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            "Accept":          (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
        })
        return session

    # ── Circuit breaker (padrão Freelance) ───────────────────────────────

    def _is_circuit_open(self) -> bool:
        return time.time() < self._circuit_open_until

    def _record_failure(self, reason: str = "") -> None:
        self._consecutive_failures += 1
        logger.debug(
            f"[{self.name}] falha #{self._consecutive_failures}"
            f"{f' ({reason})' if reason else ''}"
        )
        if self._consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
            self._circuit_open_until = time.time() + self.CIRCUIT_BREAKER_PAUSE_S
            logger.warning(
                f"[{self.name}] ⚡ Circuit breaker ABERTO — "
                f"{self._consecutive_failures} falhas consecutivas. "
                f"Pausa de {self.CIRCUIT_BREAKER_PAUSE_S}s."
            )
            self._consecutive_failures = 0

    def _record_success(self) -> None:
        if self._consecutive_failures > 0:
            logger.info(f"[{self.name}] Recuperado após {self._consecutive_failures} falhas")
        self._consecutive_failures = 0

    # ── Fetch leve (via proxy juca) ───────────────────────────────────────

    def _fetch(self, url: str, timeout: int = 15) -> Optional[str]:
        """
        GET simples via proxy residencial.
        Circuit breaker ativo: se aberto, retorna None imediatamente (não bloqueia).
        """
        if self._is_circuit_open():
            remaining = int(self._circuit_open_until - time.time())
            logger.warning(
                f"[{self.name}] ⚡ Circuit breaker aberto — {remaining}s restantes. Skip."
            )
            return None

        try:
            resp = self._session.get(url, timeout=timeout)
            if resp.status_code == 200:
                self._record_success()
                return resp.text
            logger.warning(
                f"[{self.name}] _fetch → HTTP {resp.status_code}  url={url[:70]}…"
            )
            self._record_failure(f"HTTP {resp.status_code}")
            return None
        except Exception as e:
            logger.error(f"[{self.name}] _fetch exceção: {e}  url={url[:70]}…")
            self._record_failure("exception")
            return None

    # ── Detecção de challenge ─────────────────────────────────────────────

    def _needs_browser(self, html: Optional[str]) -> bool:
        """
        Retorna True se a resposta indica challenge, captcha, ou conteúdo vazio.
        Subclasses fazem override para adicionar sinais específicos da plataforma.
        """
        if not html or len(html) < 1_000:
            return True
        check = html.lower()
        return any(s in check for s in (
            "challenge",
            "cf-browser-verification",
            "captcha",
            " robot ",
            "access denied",
            "too many requests",
            "403 forbidden",
            "you have been blocked",
        ))

    # ── Fetch pesado (Playwright) ─────────────────────────────────────────

    def _fetch_with_browser(
        self,
        url: str,
        wait_for: str = "networkidle",
    ) -> Optional[str]:
        """
        Escalação para Playwright Chromium headless.

        REGRAS (não violar):
        - 1 browser por vez — sem paralelismo (RAM / CPU)
        - Sempre fechar via `with sync_playwright()` (sem leak)
        - Roda no container VPS, SEM proxy (usa IP VPS diretamente)
          Justificativa: challenges acontecem após bloqueio de IP; Playwright
          é o fallback que vai por um caminho diferente do juca.

        Instalação necessária no Dockerfile / VPS:
          pip install playwright
          playwright install chromium --with-deps
        """
        try:
            from playwright.sync_api import sync_playwright  # import tardio — só quando usado
            logger.info(f"[{self.name}] 🎭 Playwright iniciando → {url[:70]}…")
            start = time.time()
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-extensions",
                    ],
                )
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                )
                page = ctx.new_page()
                page.goto(url, wait_until=wait_for, timeout=30_000)
                html = page.content()
                browser.close()
            elapsed = time.time() - start
            logger.info(f"[{self.name}] 🎭 Playwright OK ({elapsed:.1f}s)")
            self._record_success()
            return html
        except ImportError:
            logger.error(
                f"[{self.name}] Playwright não instalado. "
                "Execute: pip install playwright && playwright install chromium --with-deps"
            )
            return None
        except Exception as e:
            logger.error(f"[{self.name}] Playwright exceção: {e}")
            self._record_failure("playwright")
            return None

    # ── Método principal de fetch (híbrido C) ────────────────────────────

    def _fetch_robust(self, url: str) -> Optional[str]:
        """
        Método que scrapers concretos devem chamar (nunca _fetch diretamente).
        Fluxo: _fetch (leve, proxy) → _needs_browser? → _fetch_with_browser (Playwright).
        """
        html = self._fetch(url)
        if self._needs_browser(html):
            logger.info(
                f"[{self.name}] Challenge ou resposta vazia detectada → escalando Playwright"
            )
            html = self._fetch_with_browser(url)
        return html

    # ── Throttle ──────────────────────────────────────────────────────────

    def _throttle(self, seconds: Optional[float] = None) -> None:
        """Pausa entre requisições. Subclasses sobrescrevem o padrão."""
        time.sleep(seconds if seconds is not None else self.DEFAULT_THROTTLE_S)
