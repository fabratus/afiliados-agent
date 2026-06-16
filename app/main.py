"""
Agente de Afiliados - Main Application
Porta 5001 (Freelance está na 5000)
"""

import os
import logging
import threading
from flask import Flask, jsonify, render_template, request, redirect
from app.database import init_db, get_stats, get_connection
from app.ai.ml_auth import MLAuth, MLAuthError, MLNotAuthorizedError

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Inicializa banco na subida
init_db()

# Estado do scraper (para evitar execução dupla)
_scraper_state = {
    "running": False,
    "last_run": None,
    "last_result": None
}


@app.route("/")
def index():
    """Dashboard principal."""
    stats = get_stats()
    products = get_products_for_dashboard()
    return render_template(
        "index.html",
        stats=stats,
        products=products,
        scraper_state=_scraper_state
    )


@app.route("/api/health")
def health():
    """Health check endpoint."""
    return jsonify({
        "status": "ok",
        "service": "afiliados-agent",
        "version": "0.2.0-dia2"
    })


@app.route("/api/stats")
def api_stats():
    """Estatísticas gerais."""
    return jsonify(get_stats())


@app.route("/api/products")
def api_products():
    """Lista produtos (com filtros opcionais)."""
    source = request.args.get("source")
    limit = int(request.args.get("limit", 100))
    products = get_products_for_dashboard(source=source, limit=limit)
    return jsonify(products)


@app.route("/api/scrape/mercadolivre", methods=["POST"])
def scrape_mercadolivre():
    """Dispara scraper do Mercado Livre em thread separada."""
    if _scraper_state["running"]:
        return jsonify({
            "ok": False,
            "error": "Já existe um scraper rodando"
        }), 409
    
    def _run():
        from app.scrapers.mercadolivre import run_mercadolivre_scraper
        try:
            _scraper_state["running"] = True
            result = run_mercadolivre_scraper()
            _scraper_state["last_result"] = result
        except Exception as e:
            logger.error(f"Erro no scraper: {e}")
            _scraper_state["last_result"] = {"error": str(e)}
        finally:
            _scraper_state["running"] = False
            from datetime import datetime
            _scraper_state["last_run"] = datetime.now().isoformat()
    
    thread = threading.Thread(target=_run, daemon=True, name="ml-scraper")
    thread.start()
    
    return jsonify({
        "ok": True,
        "message": "Scraper iniciado em background",
        "estimated_time_seconds": 180
    })


@app.route("/api/scrape/status")
def scrape_status():
    """Status do scraper."""
    return jsonify(_scraper_state)


@app.route("/api/product/<int:product_id>", methods=["GET"])
def api_product_detail(product_id):
    """Detalhes de um produto."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM products WHERE id = ?", (product_id,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return jsonify({"error": "Produto não encontrado"}), 404
    
    return jsonify(dict(row))


@app.route("/api/product/<int:product_id>/affiliate_url", methods=["POST"])
def api_set_affiliate_url(product_id):
    """Define o link de afiliado gerado manualmente pelo usuário (ML)."""
    data = request.get_json() or {}
    affiliate_url = data.get("affiliate_url", "").strip()
    
    if not affiliate_url:
        return jsonify({"error": "affiliate_url obrigatório"}), 400
    
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        UPDATE products SET affiliate_url = ?, promoted = 1, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (affiliate_url, product_id))
    conn.commit()
    conn.close()
    
    return jsonify({"ok": True, "message": "Link de afiliado salvo"})


@app.route("/api/product/<int:product_id>/ignore", methods=["POST"])
def api_ignore_product(product_id):
    """Marca produto como ignorado."""
    conn = get_connection()
    c = conn.cursor()
    c.execute("UPDATE products SET ignored = 1 WHERE id = ?", (product_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


def get_products_for_dashboard(source=None, limit=100):
    """Retorna produtos ordenados para o dashboard."""
    conn = get_connection()
    c = conn.cursor()
    
    query = """
        SELECT id, source, external_id, title, url, affiliate_url,
               price, original_price, discount_percent, image_url,
               commission_percent, commission_value,
               rating, reviews_count, sales_count,
               seller_name, is_official_store,
               match_score, feasibility_score, ai_verdict,
               analyzed, promoted, ignored,
               created_at, last_scraped_at
        FROM products
        WHERE ignored = 0
    """
    params = []
    
    if source:
        query += " AND source = ?"
        params.append(source)
    
    query += """
        ORDER BY
            promoted DESC,
            COALESCE(match_score, 0) DESC,
            sales_count DESC
        LIMIT ?
    """
    params.append(limit)
    
    c.execute(query, params)
    rows = c.fetchall()
    conn.close()
    
    return [dict(row) for row in rows]



@app.route('/oauth/authorize')
def oauth_authorize():
    try:
        auth = MLAuth()
        url = auth.get_authorization_url()
        return redirect(url)
    except KeyError as e:
        return jsonify({'error': 'Variavel nao configurada: %s' % e}), 500


@app.route('/oauth/callback')
def oauth_callback():
    error = request.args.get('error')
    code = request.args.get('code')
    if error:
        return '<h3>Erro OAuth: %s</h3><a href=/>Voltar</a>' % error, 400
    if not code:
        return jsonify({'error': 'code ausente'}), 400
    try:
        MLAuth().exchange_code_for_token(code)
        return redirect('/?oauth=success')
    except MLAuthError as e:
        return '<h3>Falha na autorizacao: %s</h3><a href=/>Voltar</a>' % e, 500


@app.route('/api/oauth/status')
def api_oauth_status():
    auth = MLAuth()
    row = auth.load_token()
    if not row:
        return jsonify({'connected': False, 'status': 'not_authorized'})
    return jsonify({
        'connected': auth.is_connected(),
        'status': row.get('status', 'unknown'),
        'expires_at': row.get('expires_at'),
        'user_id': row.get('user_id'),
        'scope': row.get('scope'),
    })

if __name__ == "__main__":
    port = int(os.getenv("PORT", 5001))
    print(f"🚀 Afiliados Agent iniciando na porta {port}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
