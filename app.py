import os
import jwt
import bcrypt
import mercadopago
import psycopg2
import psycopg2.extras
import requests as req_ext
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template, Response
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ─── CONFIG VIA ENV ───────────────────────────────────────────────────────────
DB_URL       = os.environ["DATABASE_URL"]        # postgresql://user:pass@host:port/db
JWT_SECRET   = os.environ["JWT_SECRET"]          # string aleatória segura
MP_TOKEN     = os.environ["MP_ACCESS_TOKEN"]     # Access Token do Mercado Pago
GROQ_KEY     = os.environ["GROQ_API_KEY"]        # Chave da Groq

# ─── HELPERS ──────────────────────────────────────────────────────────────────
def get_db():
    conn = psycopg2.connect(DB_URL, cursor_factory=psycopg2.extras.RealDictCursor)
    return conn

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return jsonify({"erro": "Token ausente"}), 401
        try:
            payload = jwt.decode(auth.split(" ")[1], JWT_SECRET, algorithms=["HS256"])
        except jwt.ExpiredSignatureError:
            return jsonify({"erro": "Token expirado"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"erro": "Token inválido"}), 401
        request.admin_id = payload["id"]
        return f(*args, **kwargs)
    return decorated

# ─── AUTH / ADMIN ─────────────────────────────────────────────────────────────
@app.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM admin_usuarios WHERE email = %s", (data.get("email"),))
    user = cur.fetchone()
    cur.close(); conn.close()
    if not user or not bcrypt.checkpw(data.get("senha", "").encode(), user["senha_hash"].encode()):
        return jsonify({"erro": "Credenciais inválidas"}), 401
    token = jwt.encode(
        {"id": user["id"], "exp": datetime.utcnow() + timedelta(hours=12)},
        JWT_SECRET, algorithm="HS256"
    )
    return jsonify({"token": token, "nome": user["nome"]})

@app.route("/admin/me", methods=["GET"])
@token_required
def admin_me():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT id, nome, email, ultimo_acesso FROM admin_usuarios WHERE id = %s", (request.admin_id,))
    user = cur.fetchone()
    cur.close(); conn.close()
    return jsonify(user)

# ─── PRODUTOS (API) ───────────────────────────────────────────────────────────
@app.route("/api/produtos", methods=["GET"])
def listar_produtos():
    tipo     = request.args.get("tipo")
    categoria = request.args.get("categoria")
    conn = get_db(); cur = conn.cursor()
    filters = ["ativo = true"]
    params  = []
    if tipo:
        filters.append("tipo = %s")
        params.append(tipo)
    if categoria:
        filters.append("categoria = %s")
        params.append(categoria)
    where = " AND ".join(filters)
    cur.execute(f"SELECT * FROM produtos WHERE {where} ORDER BY destaque DESC, id DESC", params)
    produtos = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(produtos))

@app.route("/api/produtos/<slug>", methods=["GET"])
def detalhe_produto(slug):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM produtos WHERE slug = %s AND ativo = true", (slug,))
    produto = cur.fetchone()
    cur.close(); conn.close()
    if not produto:
        return jsonify({"erro": "Produto não encontrado"}), 404
    return jsonify(produto)

@app.route("/admin/produtos", methods=["POST"])
@token_required
def criar_produto():
    d = request.json
    preco = d.get("preco")
    preco = float(preco) if preco not in (None, "", "null") else 0.0
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO produtos (nome, slug, descricao, categoria, preco, arquivo_url, imagem_url, ativo, destaque, tipo)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (d["nome"], d["slug"], d.get("descricao"), d.get("categoria"), preco,
          d.get("arquivo_url"), d.get("imagem_url"), d.get("ativo", True), d.get("destaque", False),
          d.get("tipo", "loja")))
    novo_id = cur.fetchone()["id"]
    conn.commit(); cur.close(); conn.close()
    return jsonify({"id": novo_id}), 201

@app.route("/admin/produtos/<int:id>", methods=["PUT"])
@token_required
def editar_produto(id):
    d = request.json
    preco = d.get("preco")
    preco = float(preco) if preco not in (None, "", "null") else 0.0
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        UPDATE produtos SET nome=%s, slug=%s, descricao=%s, categoria=%s, preco=%s,
        arquivo_url=%s, imagem_url=%s, ativo=%s, destaque=%s, tipo=%s, updated_em=now()
        WHERE id=%s
    """, (d["nome"], d["slug"], d.get("descricao"), d.get("categoria"), preco,
          d.get("arquivo_url"), d.get("imagem_url"), d.get("ativo", True), d.get("destaque", False),
          d.get("tipo", "loja"), id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/admin/produtos/<int:id>", methods=["DELETE"])
@token_required
def deletar_produto(id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE produtos SET ativo = false WHERE id = %s", (id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

# ─── LEADS ────────────────────────────────────────────────────────────────────
@app.route("/leads", methods=["POST"])
def capturar_lead():
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO leads (nome, email, telefone, empresa, origem, interesse, status)
        VALUES (%s,%s,%s,%s,%s,%s,'novo') RETURNING id
    """, (d.get("nome"), d.get("email"), d.get("telefone"), d.get("empresa"),
          d.get("origem", "site"), d.get("interesse")))
    novo_id = cur.fetchone()["id"]
    conn.commit(); cur.close(); conn.close()
    return jsonify({"id": novo_id}), 201

@app.route("/admin/leads", methods=["GET"])
@token_required
def listar_leads():
    status = request.args.get("status")
    conn = get_db(); cur = conn.cursor()
    if status:
        cur.execute("SELECT * FROM leads WHERE status = %s ORDER BY criado_em DESC", (status,))
    else:
        cur.execute("SELECT * FROM leads ORDER BY criado_em DESC")
    leads = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(leads))

@app.route("/admin/leads/<int:id>", methods=["PUT"])
@token_required
def editar_lead(id):
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        UPDATE leads SET status=%s, obs=%s WHERE id=%s
    """, (d.get("status"), d.get("obs"), id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

# ─── PÁGINAS ──────────────────────────────────────────────────────────────────
@app.route("/negocio/<slug>")
def pagina_negocio(slug):
    return render_template("negocio.html")

@app.route("/blog")
def pagina_blog():
    return render_template("blog.html")

@app.route("/blog/<slug>")
def pagina_post(slug):
    return render_template("post.html")

@app.route("/categoria/<slug>")
def pagina_categoria(slug):
    return render_template("categoria.html")

@app.route("/seuhub")
@app.route("/seu-hub")
def pagina_seuhub():
    return render_template("seuhub.html")

@app.route("/portfolio")
def pagina_portfolio():
    return render_template("portfolio.html")

@app.route("/portfolio/<slug>")
def pagina_projeto(slug):
    return render_template("projeto.html")

@app.route("/loja")
def pagina_loja():
    return render_template("loja.html")

@app.route("/loja/<slug>")
def pagina_produto(slug):
    return render_template("produto.html")

@app.route("/<bairro_slug>")
def pagina_bairro(bairro_slug):
    # Rotas reservadas que não são bairros
    ROTAS_RESERVADAS = {
        'admin', 'blog', 'loja', 'leads', 'produtos', 'pedidos',
        'hub', 'webhook', 'obrigado', 'erro', 'politica-de-privacidade',
        'termos', 'entrar', 'minha-conta', 'redefinir-senha', 'favicon.ico',
        'portfolio', 'metricas', 'negocio', 'categoria', 'api',
        'seu-hub', 'seuhub'
    }
    if bairro_slug in ROTAS_RESERVADAS:
        return "Not Found", 404
    return render_template("bairro.html")

# ─── PEDIDOS + MERCADO PAGO ───────────────────────────────────────────────────
@app.route("/pedidos", methods=["POST"])
def criar_pedido():
    d = request.json
    conn = get_db(); cur = conn.cursor()

    # Busca produto
    cur.execute("SELECT * FROM produtos WHERE id = %s AND ativo = true", (d["produto_id"],))
    produto = cur.fetchone()
    if not produto:
        cur.close(); conn.close()
        return jsonify({"erro": "Produto não encontrado"}), 404

    # Cria pedido
    cur.execute("""
        INSERT INTO pedidos (produto_id, lead_id, valor, status)
        VALUES (%s,%s,%s,'pendente') RETURNING id
    """, (produto["id"], d["lead_id"], produto["preco"]))
    pedido_id = cur.fetchone()["id"]
    conn.commit()

    # Gera link Mercado Pago
    sdk = mercadopago.SDK(MP_TOKEN)
    preference = sdk.preference().create({
        "items": [{
            "title": produto["nome"],
            "quantity": 1,
            "unit_price": float(produto["preco"])
        }],
        "external_reference": str(pedido_id),
        "notification_url": os.environ.get("MP_WEBHOOK_URL", ""),
        "back_urls": {
            "success": os.environ.get("MP_SUCCESS_URL", ""),
            "failure": os.environ.get("MP_FAILURE_URL", ""),
        },
        "auto_return": "approved"
    })

    link = preference["response"]["init_point"]
    cur.close(); conn.close()
    return jsonify({"pedido_id": pedido_id, "link_pagamento": link}), 201

@app.route("/webhook/mp", methods=["POST"])
def webhook_mp():
    data = request.json or {}
    if data.get("type") != "payment":
        return jsonify({"ok": True})

    sdk = mercadopago.SDK(MP_TOKEN)
    payment_id = data.get("data", {}).get("id")
    pagamento = sdk.payment().get(payment_id)
    info = pagamento["response"]

    if info.get("status") == "approved":
        pedido_id = int(info.get("external_reference", 0))
        conn = get_db(); cur = conn.cursor()
        cur.execute("""
            UPDATE pedidos SET status='pago', mp_payment_id=%s WHERE id=%s
        """, (str(payment_id), pedido_id))
        conn.commit(); cur.close(); conn.close()

    return jsonify({"ok": True})

@app.route("/admin/pedidos", methods=["GET"])
@token_required
def listar_pedidos():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT p.*, pr.nome as produto_nome, l.nome as lead_nome, l.email as lead_email
        FROM pedidos p
        JOIN produtos pr ON pr.id = p.produto_id
        JOIN leads l ON l.id = p.lead_id
        ORDER BY p.criado_em DESC
    """)
    pedidos = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(pedidos))

# ─── GERAR HUB COM GROQ (proxy seguro — chave fica no servidor) ───────────────
@app.route("/api/gerar-hub", methods=["POST"])
def gerar_hub():
    d = request.json or {}
    site = d.get("site", "").strip()
    desc = d.get("descricao", "").strip()

    if not site and not desc:
        return jsonify({"erro": "Informe ao menos o site ou a descrição do negócio."}), 400

    prompt = f"""Você é um consultor sênior de SEO e marketing digital da Leanttro Tecnologia. \
O serviço "Seu Hub" instala um hub de negócios locais dentro do domínio do cliente, \
gerando tráfego orgânico no Google sem pagar por anúncios.

Dados do cliente:
- Site: {site or "não informado"}
- Descrição do negócio: {desc or "não informada"}

Com base nesses dados, gere EXATAMENTE 3 ideias de hub personalizadas para este cliente. \
Responda APENAS com JSON válido, sem texto antes ou depois, sem markdown, sem ```json. \
Use este formato exato:
{{"hubs":[{{"titulo":"...","nicho":"...","keyword_exemplo":"...","potencial":"...","por_que":"..."}},{{"titulo":"...","nicho":"...","keyword_exemplo":"...","potencial":"...","por_que":"..."}},{{"titulo":"...","nicho":"...","keyword_exemplo":"...","potencial":"...","por_que":"..."}}]}}

Regras:
- titulo: nome criativo e direto do hub (ex: "Hub de Academias do Jabaquara")
- nicho: segmento/público que seria listado (ex: "academias, crossfit e pilates do bairro")
- keyword_exemplo: uma keyword real que esse hub rankearia (ex: "academia perto do Jabaquara")
- potencial: estimativa realista de resultado em 3-6 meses baseada nos cases: \
feirasderua.com.br com 328 mil impressões em 3 meses, guiadorodizio.com.br rankeando em 4 dias
- por_que: 1 frase explicando por que faz sentido para o negócio deste cliente especificamente"""

    try:
        r = req_ext.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 800,
                "temperature": 0.7
            },
            timeout=30
        )
        data = r.json()
        texto = data["choices"][0]["message"]["content"]
        return jsonify({"resultado": texto})
    except Exception as e:
        return jsonify({"erro": "Falha ao conectar com a IA. Tente novamente."}), 500

# ─── HUB DE NEGÓCIOS ──────────────────────────────────────────────────────────
@app.route("/hub/categorias", methods=["GET"])
def listar_hub_categorias():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM hub_categorias WHERE ativo = true ORDER BY nome")
    cats = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(cats))

@app.route("/admin/hub/categorias", methods=["POST"])
@token_required
def criar_categoria():
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO hub_categorias (nome, slug, icone_url, ativo)
        VALUES (%s,%s,%s,%s) RETURNING id
    """, (d["nome"], d["slug"], d.get("icone_url"), d.get("ativo", True)))
    novo_id = cur.fetchone()["id"]
    conn.commit(); cur.close(); conn.close()
    return jsonify({"id": novo_id}), 201

@app.route("/admin/hub/categorias/<int:id>", methods=["PUT"])
@token_required
def editar_categoria(id):
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        UPDATE hub_categorias SET nome=%s, slug=%s, icone_url=%s, ativo=%s
        WHERE id=%s
    """, (d["nome"], d["slug"], d.get("icone_url"), d.get("ativo", True), id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/admin/hub/categorias/<int:id>", methods=["DELETE"])
@token_required
def deletar_categoria(id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE hub_categorias SET ativo = false WHERE id = %s", (id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/hub/negocios", methods=["GET"])
def listar_hub_negocios():
    categoria = request.args.get("categoria")
    bairro    = request.args.get("bairro")
    conn = get_db(); cur = conn.cursor()
    filters = ["n.ativo = true"]
    params  = []
    if categoria:
        filters.append("c.slug = %s")
        params.append(categoria)
    if bairro:
        filters.append("LOWER(n.bairro) LIKE %s")
        params.append(f"%{bairro.lower()}%")
    where = " AND ".join(filters)
    cur.execute(f"""
        SELECT n.*, c.nome as categoria_nome, c.slug as categoria_slug
        FROM hub_negocios n
        JOIN hub_categorias c ON c.id = n.categoria_id
        WHERE {where}
        ORDER BY n.destaque DESC, n.id DESC
    """, params)
    negocios = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(negocios))

@app.route("/hub/negocios/<slug>", methods=["GET"])
def detalhe_negocio(slug):
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT n.*, c.nome as categoria_nome FROM hub_negocios n
        JOIN hub_categorias c ON c.id = n.categoria_id
        WHERE n.slug = %s AND n.ativo = true
    """, (slug,))
    negocio = cur.fetchone()
    if not negocio:
        cur.close(); conn.close()
        return jsonify({"erro": "Negócio não encontrado"}), 404

    # Incrementa visualizações
    cur.execute("UPDATE hub_negocios SET visualizacoes = visualizacoes + 1 WHERE slug = %s", (slug,))

    # Busca avaliações
    cur.execute("SELECT * FROM hub_avaliacoes WHERE negocio_id = %s ORDER BY criado_em DESC", (negocio["id"],))
    avaliacoes = cur.fetchall()

    conn.commit(); cur.close(); conn.close()
    return jsonify({**negocio, "avaliacoes": list(avaliacoes)})

@app.route("/hub/avaliacoes", methods=["GET"])
def listar_avaliacoes():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT a.*, n.nome as negocio_nome FROM hub_avaliacoes a
        JOIN hub_negocios n ON n.id = a.negocio_id
        ORDER BY a.criado_em DESC
    """)
    avaliacoes = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(avaliacoes))

@app.route("/hub/avaliacoes", methods=["POST"])
def criar_avaliacao():
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO hub_avaliacoes (negocio_id, nota, comentario, autor_nome)
        VALUES (%s,%s,%s,%s) RETURNING id
    """, (d["negocio_id"], d["nota"], d.get("comentario"), d.get("autor_nome")))
    novo_id = cur.fetchone()["id"]
    conn.commit(); cur.close(); conn.close()
    return jsonify({"id": novo_id}), 201

@app.route("/admin/hub/negocios", methods=["POST"])
@token_required
def criar_negocio():
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO hub_negocios (categoria_id, nome, slug, endereco, bairro, cidade,
        lat, lng, telefone, whatsapp, site_url, foto_url, descricao, plano, destaque)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (d["categoria_id"], d["nome"], d["slug"], d.get("endereco"), d.get("bairro"),
          d.get("cidade", "São Paulo"), d.get("lat"), d.get("lng"), d.get("telefone"),
          d.get("whatsapp"), d.get("site_url"), d.get("foto_url"), d.get("descricao"),
          d.get("plano", "gratuito"), d.get("destaque", False)))
    novo_id = cur.fetchone()["id"]
    conn.commit(); cur.close(); conn.close()
    return jsonify({"id": novo_id}), 201

@app.route("/admin/hub/negocios/<int:id>", methods=["PUT"])
@token_required
def editar_negocio(id):
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        UPDATE hub_negocios SET categoria_id=%s, nome=%s, slug=%s, endereco=%s, bairro=%s,
        cidade=%s, lat=%s, lng=%s, telefone=%s, whatsapp=%s, site_url=%s, foto_url=%s,
        descricao=%s, plano=%s, destaque=%s, ativo=%s, updated_em=now()
        WHERE id=%s
    """, (d["categoria_id"], d["nome"], d["slug"], d.get("endereco"), d.get("bairro"),
          d.get("cidade"), d.get("lat"), d.get("lng"), d.get("telefone"), d.get("whatsapp"),
          d.get("site_url"), d.get("foto_url"), d.get("descricao"), d.get("plano"),
          d.get("destaque"), d.get("ativo", True), id))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

# ─── BLOG ─────────────────────────────────────────────────────────────────────
@app.route("/admin/blog", methods=["GET"])
@token_required
def listar_posts_admin():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT id, titulo, slug, resumo, categoria, imagem_url, publicado, publicado_em, criado_em
        FROM blog_posts ORDER BY criado_em DESC
    """)
    posts = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(posts))

@app.route("/api/blog", methods=["GET"])
def listar_posts():
    categoria = request.args.get("categoria")
    conn = get_db(); cur = conn.cursor()
    if categoria:
        cur.execute("""
            SELECT id, titulo, slug, resumo, categoria, imagem_url, publicado_em
            FROM blog_posts WHERE publicado = true AND categoria = %s
            ORDER BY publicado_em DESC
        """, (categoria,))
    else:
        cur.execute("""
            SELECT id, titulo, slug, resumo, categoria, imagem_url, publicado_em
            FROM blog_posts WHERE publicado = true
            ORDER BY publicado_em DESC
        """)
    posts = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(posts))

@app.route("/api/blog/<slug>", methods=["GET"])
def detalhe_post(slug):
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM blog_posts WHERE slug = %s AND publicado = true", (slug,))
    post = cur.fetchone()
    if not post:
        cur.close(); conn.close()
        return jsonify({"erro": "Post não encontrado"}), 404
    cur.execute("SELECT tag FROM blog_tags WHERE post_id = %s", (post["id"],))
    tags = [r["tag"] for r in cur.fetchall()]
    cur.close(); conn.close()
    return jsonify({**post, "tags": tags})

@app.route("/admin/blog", methods=["POST"])
@token_required
def criar_post():
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO blog_posts (titulo, slug, resumo, conteudo, categoria, imagem_url, publicado, publicado_em)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (d["titulo"], d["slug"], d.get("resumo"), d.get("conteudo"), d.get("categoria"),
          d.get("imagem_url"), d.get("publicado", False),
          datetime.utcnow() if d.get("publicado") else None))
    post_id = cur.fetchone()["id"]
    for tag in d.get("tags", []):
        cur.execute("INSERT INTO blog_tags (post_id, tag) VALUES (%s,%s)", (post_id, tag))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"id": post_id}), 201

@app.route("/admin/blog/<int:id>", methods=["PUT"])
@token_required
def editar_post(id):
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        UPDATE blog_posts SET titulo=%s, slug=%s, resumo=%s, conteudo=%s, categoria=%s,
        imagem_url=%s, publicado=%s, updated_em=now()
        WHERE id=%s
    """, (d["titulo"], d["slug"], d.get("resumo"), d.get("conteudo"), d.get("categoria"),
          d.get("imagem_url"), d.get("publicado", False), id))
    cur.execute("DELETE FROM blog_tags WHERE post_id = %s", (id,))
    for tag in d.get("tags", []):
        cur.execute("INSERT INTO blog_tags (post_id, tag) VALUES (%s,%s)", (id, tag))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

# ─── DISPAROS ─────────────────────────────────────────────────────────────────
@app.route("/admin/disparos", methods=["GET"])
@token_required
def listar_disparos():
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        SELECT d.*, l.nome as lead_nome FROM disparos d
        JOIN leads l ON l.id = d.lead_id
        ORDER BY d.enviado_em DESC
    """)
    disparos = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(disparos))

@app.route("/admin/disparos", methods=["POST"])
@token_required
def registrar_disparo():
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO disparos (lead_id, canal, assunto, mensagem, status)
        VALUES (%s,%s,%s,%s,'enviado') RETURNING id
    """, (d["lead_id"], d.get("canal"), d.get("assunto"), d.get("mensagem")))
    novo_id = cur.fetchone()["id"]
    conn.commit(); cur.close(); conn.close()
    return jsonify({"id": novo_id}), 201

# ─── SITEMAP ──────────────────────────────────────────────────────────────────
@app.route("/sitemap.xml", methods=["GET"])
def sitemap():
    BASE_URL = os.environ.get("BASE_URL", "https://leanttro.com.br").rstrip("/")
    now = datetime.utcnow().strftime("%Y-%m-%d")

    urls = []

    # Páginas estáticas
    estaticas = [
        ("", "1.0", "daily"),
        ("/blog", "0.8", "daily"),
        ("/loja", "0.8", "weekly"),
        ("/portfolio", "0.7", "weekly"),
        ("/seuhub", "0.8", "weekly"),
        ("/metricas", "0.6", "monthly"),
    ]
    for path, priority, freq in estaticas:
        urls.append(f"""  <url>
    <loc>{BASE_URL}{path}</loc>
    <lastmod>{now}</lastmod>
    <changefreq>{freq}</changefreq>
    <priority>{priority}</priority>
  </url>""")

    conn = get_db(); cur = conn.cursor()

    # Posts do blog
    cur.execute("SELECT slug, publicado_em FROM blog_posts WHERE publicado = true ORDER BY publicado_em DESC")
    for row in cur.fetchall():
        data = row["publicado_em"].strftime("%Y-%m-%d") if row["publicado_em"] else now
        urls.append(f"""  <url>
    <loc>{BASE_URL}/blog/{row['slug']}</loc>
    <lastmod>{data}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.7</priority>
  </url>""")

    # Produtos da loja
    cur.execute("SELECT slug, updated_em FROM produtos WHERE ativo = true ORDER BY id DESC")
    for row in cur.fetchall():
        data = row["updated_em"].strftime("%Y-%m-%d") if row.get("updated_em") else now
        urls.append(f"""  <url>
    <loc>{BASE_URL}/loja/{row['slug']}</loc>
    <lastmod>{data}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.7</priority>
  </url>""")

    # Negócios do hub
    cur.execute("SELECT slug, updated_em FROM hub_negocios WHERE ativo = true ORDER BY id DESC")
    for row in cur.fetchall():
        data = row["updated_em"].strftime("%Y-%m-%d") if row.get("updated_em") else now
        urls.append(f"""  <url>
    <loc>{BASE_URL}/negocio/{row['slug']}</loc>
    <lastmod>{data}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>""")

    # Categorias do hub (cada uma é uma página template)
    cur.execute("SELECT slug FROM hub_categorias WHERE ativo = true ORDER BY nome")
    for row in cur.fetchall():
        urls.append(f"""  <url>
    <loc>{BASE_URL}/categoria/{row['slug']}</loc>
    <lastmod>{now}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>""")

    # Bairros com negócios cadastrados (cada bairro é uma página template)
    cur.execute("""
        SELECT DISTINCT bairro FROM hub_negocios
        WHERE ativo = true AND bairro IS NOT NULL AND bairro != ''
        ORDER BY bairro
    """)
    for row in cur.fetchall():
        bairro_slug = (
            row["bairro"].lower()
            .replace(" ", "-")
            .replace("ã", "a").replace("â", "a").replace("á", "a").replace("à", "a")
            .replace("ê", "e").replace("é", "e")
            .replace("í", "i")
            .replace("ô", "o").replace("ó", "o").replace("õ", "o")
            .replace("ú", "u").replace("ü", "u")
            .replace("ç", "c")
        )
        urls.append(f"""  <url>
    <loc>{BASE_URL}/{bairro_slug}</loc>
    <lastmod>{now}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>""")

    cur.close(); conn.close()

    xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "\n".join(urls)
    xml += "\n</urlset>"

    return Response(xml, mimetype="application/xml")

# ─── MÉTRICAS PÚBLICA (Leanttro) ──────────────────────────────────────────────
@app.route("/metricas")
def pagina_metricas():
    return render_template("metricas.html")

# ─── PAINEL ADMIN ─────────────────────────────────────────────────────────────
@app.route("/admin")
def painel():
    return render_template("painel.html")

@app.route("/")
def index():
    return render_template("index.html")

# ─── RUN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
