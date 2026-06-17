#!/usr/bin/env python3
"""
Passo 0 — Pré-check ML API Endpoints
======================================
Confirma quais endpoints respondem 200 com o token OAuth atual.
Resultado desta rodada define Path A (API completa) vs Path B (fallback HTML).

Rodar no VPS:
  cd /home/deploy/afiliados-agent
  python3 scripts/passo0_check_ml_endpoints.py

Antes de rodar: ajuste MLB_ID com um ID real de produto ML.
Como obter um MLB_ID:
  Acesse https://www.mercadolivre.com.br e pesquise qualquer produto.
  Na URL ou no card, o padrão é MLB seguido de 8-12 dígitos.
  Ex: https://www.mercadolivre.com.br/fone/.../p/MLB23847609
      →  MLB23847609
"""

import sys
import os
import sqlite3
import json
import time

# ─── Path para importar o projeto ────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# ─── CONFIGURE ESTE CAMPO ANTES DE RODAR ─────────────────────────────────────
MLB_ID = "MLB_XXXXX"   # fone bluetooth — produto real para teste   # ← SUBSTITUIR por um ID real (ver instruções acima)
# ─────────────────────────────────────────────────────────────────────────────

# Sentinel para detectar que MLB_ID não foi configurado
_MLB_NOT_CONFIGURED = False  # MLB_ID configurado com produto real

import requests

BASE_API = "https://api.mercadolibre.com"
TIMEOUT  = 12


# ─── Obter token (tenta ml_auth.py; cai no banco se falhar) ──────────────────

def _get_token_from_db() -> str | None:
    """Lê o último access_token válido direto do SQLite."""
    db_path = os.path.join(PROJECT_ROOT, "data", "agent.db")
    if not os.path.exists(db_path):
        return None
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT access_token, expires_at FROM oauth_tokens ORDER BY id DESC LIMIT 1"
        ).fetchone()
        conn.close()
        if not row:
            return None
        access_token, expires_at = row
        from datetime import datetime, timezone
        try:
            exp = datetime.fromisoformat(str(expires_at))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc).timestamp() >= exp.timestamp() - 60:
                print("⚠️  Token expirado no banco — tente refresh via dashboard antes de rodar.")
        except Exception:
            pass
        if False:
            print("⚠️  Token expirado no banco — tente refresh via dashboard antes de rodar.")
        return access_token
    except Exception as e:
        print(f"⚠️  Erro ao ler DB: {e}")
        return None


def get_token() -> str | None:
    """
    Tenta importar ml_auth.py (Sessão G).
    Nomes comuns que o módulo pode exportar — ajuste se necessário.
    """
    for fn_name in ("get_valid_token", "get_access_token", "get_token", "fetch_token"):
        try:
            mod = __import__("app.ai.ml_auth", fromlist=[fn_name])
            fn  = getattr(mod, fn_name, None)
            if callable(fn):
                result = fn()
                if result:
                    return result
        except (ImportError, Exception):
            pass

    # Fallback direto no banco
    return _get_token_from_db()


# ─── Helper de check ─────────────────────────────────────────────────────────

def check(session: requests.Session, url: str, label: str) -> tuple[int | None, dict | None]:
    try:
        r = session.get(url, timeout=TIMEOUT)
        status = r.status_code
        icon   = "✅" if status == 200 else ("🔴" if status == 403 else "⚠️ ")
        detail = ""
        if status != 200:
            try:
                detail = r.json().get("message", r.text[:60])
            except Exception:
                detail = r.text[:60]
        print(f"  {icon}  {label:<52} → HTTP {status}  {detail}")
        body = r.json() if status == 200 else None
        return status, body
    except Exception as e:
        print(f"  ❌  {label:<52} → ERRO: {e}")
        return None, None


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 66)
    print("  Passo 0 — Pré-check ML API Endpoints (Sessão L)")
    print("=" * 66 + "\n")

    if _MLB_NOT_CONFIGURED:
        print("❌  ATENÇÃO: MLB_ID não foi configurado.")
        print("    Edite a variável MLB_ID no início deste script com um ID real.")
        print("    Ex: MLB_ID = 'MLB23847609'")
        print()

    # ─── Token ──────────────────────────────────────────────────────────
    print("🔑  Obtendo token OAuth...")
    token = get_token()
    if not token:
        print("\n❌  Token não encontrado.")
        print("    Faça login via dashboard (botão OAuth) e rode novamente.")
        sys.exit(1)
    print(f"    Token obtido: {token[:12]}...{token[-6:]}\n")

    session = requests.Session()
    session.headers["Authorization"] = f"Bearer {token}"

    # ─── 1. Sanity check ────────────────────────────────────────────────
    print("── Sanity check ──────────────────────────────────────────────")
    status, body = check(session, f"{BASE_API}/users/me", "/users/me  (valida token)")
    if status != 200:
        print("\n❌  Token inválido ou expirado. Reautorize e rode de novo.")
        sys.exit(1)
    my_id = body.get("id") if body else None
    print(f"    Usuário autenticado: id={my_id}\n")

    # ─── 2. Endpoints de enriquecimento ─────────────────────────────────
    print("── Enriquecimento (descoberta de produtos) ───────────────────")
    if _MLB_NOT_CONFIGURED:
        print("  ⚠️  MLB_ID não configurado — testando com placeholder (espere 404)")
    print()

    _, items_body = check(session, f"{BASE_API}/items/{MLB_ID}",
                          f"/items/{{id}}  (detalhe)")

    # Extrair seller_id da resposta de /items para usar no teste de /users
    seller_id = "000000000"
    if items_body:
        raw_seller = items_body.get("seller_id")
        if raw_seller:
            seller_id = str(raw_seller)
            print(f"    → seller_id extraído da resposta: {seller_id}")

    _, desc_body = check(session, f"{BASE_API}/items/{MLB_ID}/description",
                         f"/items/{{id}}/description")
    _, rev_body  = check(session, f"{BASE_API}/reviews/item/{MLB_ID}",
                         f"/reviews/item/{{id}}")
    _, usr_body  = check(session, f"{BASE_API}/users/{seller_id}",
                         f"/users/{{seller_id}}  (reputação)")

    # ─── 3. Busca alternativa (pode mudar o plano se 200) ───────────────
    print()
    print("── Endpoint de busca (avaliar substituir scraping se 200) ────")
    search_status, search_body = check(
        session,
        f"{BASE_API}/products/search?site_id=MLB&q=notebook",
        "/products/search  (busca alternativa)"
    )

    # ─── 4. Resumo e recomendação ────────────────────────────────────────
    print()
    print("=" * 66)
    print("  RESULTADO")
    print("=" * 66)

    results = {
        "/items/{id}":              items_body is not None,
        "/items/{id}/description":  desc_body  is not None,
        "/reviews/item/{id}":       rev_body   is not None,
        "/users/{seller_id}":       usr_body   is not None,
    }
    open_enrich   = [k for k, v in results.items() if v]
    closed_enrich = [k for k, v in results.items() if not v]

    print(f"\n  Enriquecimento via API: {len(open_enrich)}/4 endpoints abertos")
    for ep in open_enrich:
        print(f"    ✅  {ep}")
    for ep in closed_enrich:
        print(f"    🔴  {ep}  → plano B: extrair do HTML do produto")

    if search_status == 200:
        print()
        print("  ⭐  /products/search respondeu 200 !")
        print("      AÇÃO NECESSÁRIA: avaliar usar no lugar do scraping HTML.")
        print("      Inspecione search_body (abaixo) e veja se tem title/price/seller_id.")
        if search_body:
            sample = json.dumps(search_body, ensure_ascii=False)[:300]
            print(f"      Amostra: {sample}...")

    print()
    if len(open_enrich) == 4:
        print("  → PATH A (completo): _enrich via API cobre tudo. ✅ Implementar conforme spec.")
    elif len(open_enrich) >= 2:
        print("  → PATH A parcial: _enrich cobre só os endpoints abertos;")
        print("    para os fechados, extrai do HTML da página do produto.")
    elif len(open_enrich) == 0:
        print("  → PATH B: sem enriquecimento via API. Usar scraping da página de produto.")
    else:
        print(f"  → PATH A parcial ({len(open_enrich)}/4 endpoints). Ver detalhes acima.")

    print()
    print("  Cole esta tabela para o Claude decidir ajustes no _enrich.\n")


if __name__ == "__main__":
    main()
