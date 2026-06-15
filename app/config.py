"""
Configuração do sistema - Agente de Afiliados
Termos de busca, categorias e parâmetros gerais
"""

# ========================================
# MERCADO LIVRE - Termos de busca
# ========================================
# Termos variados para garimpar produtos em diferentes nichos
# Edite conforme seus interesses
ML_SEARCH_TERMS = [
    # Eletrônicos
    "fone bluetooth",
    "smartwatch",
    "caixa de som",
    "carregador portatil",
    
    # Casa
    "air fryer",
    "aspirador robo",
    "liquidificador",
    "cafeteira",
    
    # Beleza e cuidado pessoal
    "secador de cabelo",
    "escova modeladora",
    "barbeador eletrico",
    
    # Fitness
    "tapete yoga",
    "halteres",
    "faixa elastica",
    
    # Moda/acessórios
    "mochila notebook",
    "relogio masculino",
    
    # Pet
    "racao caes",
    "arranhador gato",
    
    # Automotivo
    "capa volante",
    "cheirinho carro",
    
    # Utilidades
    "organizador armario",
    "luminaria mesa",
]

# ========================================
# MERCADO LIVRE - Categorias estratégicas
# ========================================
# IDs de categorias mais vendidas no Mercado Livre Brasil
# Fonte: https://api.mercadolibre.com/sites/MLB/categories
ML_CATEGORIES = [
    ("MLB1051", "Celulares e Telefones"),
    ("MLB1648", "Informatica"),
    ("MLB5726", "Eletrodomesticos"),
    ("MLB1000", "Eletronicos Audio e Video"),
    ("MLB1276", "Esportes e Fitness"),
    ("MLB1246", "Beleza e Cuidado Pessoal"),
    ("MLB1574", "Casa Moveis e Decoracao"),
    ("MLB1132", "Brinquedos e Hobbies"),
]

# ========================================
# LIMITES DE COLETA
# ========================================
ML_PRODUCTS_PER_TERM = 20      # quantos produtos por termo de busca
ML_PRODUCTS_PER_CATEGORY = 30  # quantos produtos por categoria
ML_REQUEST_DELAY = 1.0         # segundos entre requisições (boas práticas)
ML_MAX_PRODUCTS_PER_RUN = 300  # limite total por execução

# ========================================
# FILTROS BÁSICOS NA COLETA
# ========================================
# Produtos que NÃO passam nesses filtros são descartados direto
ML_MIN_PRICE = 20.0            # R$ mínimo (comissão baixa demais)
ML_MAX_PRICE = 5000.0          # R$ máximo (produtos muito caros = baixa conversão)
ML_MIN_REVIEWS = 5             # reviews mínimos para considerar
ML_MIN_SOLD = 10               # vendas mínimas (produtos já validados)

# ========================================
# COMISSÕES POR CATEGORIA (estimativa ML)
# ========================================
# ML paga comissões variáveis por categoria (programa de afiliados)
# Valores abaixo são ESTIMATIVAS baseadas no programa atual
# Fonte: https://www.mercadolivre.com.br/afiliados
ML_COMMISSION_BY_CATEGORY = {
    "MLB1051": 3.5,   # Celulares - 3.5%
    "MLB1648": 4.0,   # Informatica - 4%
    "MLB5726": 4.5,   # Eletrodomesticos - 4.5%
    "MLB1000": 4.0,   # Audio e Video - 4%
    "MLB1276": 5.0,   # Esportes - 5%
    "MLB1246": 6.0,   # Beleza - 6%
    "MLB1574": 5.0,   # Casa - 5%
    "MLB1132": 5.0,   # Brinquedos - 5%
    "default": 4.0    # fallback
}
