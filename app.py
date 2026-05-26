import os
import json
import datetime as _dt
import jwt
import bcrypt
import mercadopago
import psycopg2
import psycopg2.extras
import requests as req_ext
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template, Response, session, redirect
from flask_cors import CORS
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

app = Flask(__name__)
CORS(app)
app.secret_key = os.environ.get("SECRET_KEY", "troca-isso")
app.config["SESSION_COOKIE_SECURE"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

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
    preco_json = d.get("preco_json")
    if preco_json is not None and not isinstance(preco_json, str):
        preco_json = json.dumps(preco_json)
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO produtos (nome, slug, descricao, categoria, preco, preco_json, arquivo_url, imagem_url, ativo, destaque, tipo)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (d["nome"], d["slug"], d.get("descricao"), d.get("categoria"), preco, preco_json,
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
    preco_json = d.get("preco_json")
    if preco_json is not None and not isinstance(preco_json, str):
        preco_json = json.dumps(preco_json)
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        UPDATE produtos SET nome=%s, slug=%s, descricao=%s, categoria=%s, preco=%s, preco_json=%s,
        arquivo_url=%s, imagem_url=%s, ativo=%s, destaque=%s, tipo=%s, updated_em=now()
        WHERE id=%s
    """, (d["nome"], d["slug"], d.get("descricao"), d.get("categoria"), preco, preco_json,
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

@app.route("/termos")
def pagina_termos():
    return render_template("termos.html")

@app.route("/privacidade")
def pagina_privacidade():
    return render_template("privacidade.html")

@app.route("/<bairro_slug>")
def pagina_bairro(bairro_slug):
    # Rotas reservadas que não são bairros
    ROTAS_RESERVADAS = {
        'admin', 'blog', 'loja', 'leads', 'produtos', 'pedidos',
        'hub', 'webhook', 'obrigado', 'erro', 'politica-de-privacidade',
        'termos', 'privacidade', 'entrar', 'minha-conta', 'redefinir-senha', 'favicon.ico',
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
O serviço \"Seu Hub\" instala um hub de negócios locais dentro do domínio do cliente, \
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

@app.route("/admin/hub/negocios/<int:id>", methods=["DELETE"])
@token_required
def deletar_negocio(id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE hub_negocios SET ativo = false WHERE id = %s", (id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/admin/hub/negocios/bulk", methods=["DELETE"])
@token_required
def bulk_deletar_negocios():
    ids = request.json.get("ids", [])
    if not ids:
        return jsonify({"erro": "Nenhum ID informado"}), 400
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE hub_negocios SET ativo = false WHERE id = ANY(%s)", (ids,))
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

@app.route("/admin/blog/<int:id>", methods=["DELETE"])
@token_required
def deletar_post(id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE blog_posts SET publicado = false WHERE id = %s", (id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/admin/hub/avaliacoes/<int:id>", methods=["DELETE"])
@token_required
def deletar_avaliacao(id):
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM hub_avaliacoes WHERE id = %s", (id,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/admin/produtos/bulk", methods=["DELETE"])
@token_required
def bulk_deletar_produtos():
    ids = request.json.get("ids", [])
    if not ids:
        return jsonify({"erro": "Nenhum ID informado"}), 400
    conn = get_db(); cur = conn.cursor()
    cur.execute("UPDATE produtos SET ativo = false WHERE id = ANY(%s)", (ids,))
    conn.commit(); cur.close(); conn.close()
    return jsonify({"ok": True})

@app.route("/admin/leads/bulk", methods=["DELETE"])
@token_required
def bulk_deletar_leads():
    ids = request.json.get("ids", [])
    if not ids:
        return jsonify({"erro": "Nenhum ID informado"}), 400
    conn = get_db(); cur = conn.cursor()
    cur.execute("DELETE FROM leads WHERE id = ANY(%s)", (ids,))
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
    BASE_URL = os.environ.get("BASE_URL", "https://www.leanttro.com.br").rstrip("/")
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

    # Categorias do hub
    cur.execute("SELECT slug FROM hub_categorias WHERE ativo = true ORDER BY nome")
    for row in cur.fetchall():
        urls.append(f"""  <url>
    <loc>{BASE_URL}/categoria/{row['slug']}</loc>
    <lastmod>{now}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.8</priority>
  </url>""")

    # Bairros com negócios cadastrados
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


# ═══════════════════════════════════════════════════════════════════
# BLOCO: MÉTRICAS — GOOGLE OAUTH2 + GSC + GA4  (multi-tenant por GA ID)
# ═══════════════════════════════════════════════════════════════════

# ── Config ──────────────────────────────────────────────────────────
GOOGLE_CLIENT_ID     = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI  = os.environ.get("GOOGLE_REDIRECT_URI", "")

SCOPES_METRICAS = [
    'https://www.googleapis.com/auth/webmasters.readonly',
    'https://www.googleapis.com/auth/analytics.readonly',
]

# Arquivo onde os tokens são salvos por GA ID (ex: G-H7F6WRRVS7)
BASE_DIR     = os.path.dirname(os.path.abspath(__file__))
TOKENS_FILE  = os.path.join(BASE_DIR, 'data', 'metricas_tokens.json')


# ── Helpers de token ────────────────────────────────────────────────

def _load_tokens() -> dict:
    try:
        if os.path.exists(TOKENS_FILE):
            with open(TOKENS_FILE, 'r') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _save_tokens(data: dict):
    os.makedirs(os.path.dirname(TOKENS_FILE), exist_ok=True)
    with open(TOKENS_FILE, 'w') as f:
        json.dump(data, f)


def _get_creds(ga_id: str):
    """Retorna Credentials válidas para o GA ID informado, ou None."""
    tokens = _load_tokens()
    tok    = tokens.get(ga_id)
    if not tok:
        return None
    creds = Credentials(
        token         = tok.get('token'),
        refresh_token = tok.get('refresh_token'),
        token_uri     = 'https://oauth2.googleapis.com/token',
        client_id     = GOOGLE_CLIENT_ID,
        client_secret = GOOGLE_CLIENT_SECRET,
        scopes        = SCOPES_METRICAS,
    )
    if creds.expired and creds.refresh_token:
        try:
            from google.auth.transport.requests import Request as GRequest
            creds.refresh(GRequest())
            tok['token']   = creds.token
            tokens[ga_id]  = tok
            _save_tokens(tokens)
        except Exception as e:
            print(f"[metricas] Falha ao renovar token para {ga_id}: {e}")
            return None
    return creds


def _flow():
    """Cria o Flow OAuth padrão."""
    return Flow.from_client_config(
        {
            "web": {
                "client_id"    : GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri"     : "https://accounts.google.com/o/oauth2/auth",
                "token_uri"    : "https://oauth2.googleapis.com/token",
                "redirect_uris": [GOOGLE_REDIRECT_URI],
            }
        },
        scopes       = SCOPES_METRICAS,
        redirect_uri = GOOGLE_REDIRECT_URI,
    )


# ── Rotas ────────────────────────────────────────────────────────────

@app.route('/api/metricas/status')
def metricas_status():
    """Retorna se o GA ID da sessão já tem token válido."""
    ga_id = request.args.get('ga_id', '').upper().strip()
    if not ga_id:
        return jsonify({"success": False, "error": "ga_id não informado"}), 400

    creds  = _get_creds(ga_id)
    tokens = _load_tokens()
    tok    = tokens.get(ga_id, {})
    return jsonify({
        "success"        : True,
        "conectado"      : creds is not None and creds.valid,
        "gsc_site"       : tok.get('gsc_site', ''),
        "ga4_property"   : tok.get('ga4_property', ''),
        "all_properties" : tok.get('all_properties', []),
        "gsc_sites"      : tok.get('gsc_sites', []),
    })


@app.route('/api/metricas/oauth/start')
def metricas_oauth_start():
    """Inicia o fluxo OAuth. Recebe ?ga_id=G-XXXXX na query."""
    ga_id = request.args.get('ga_id', '').upper().strip()
    if not ga_id or not ga_id.startswith('G-'):
        return "GA ID inválido. Ex: G-H7F6WRRVS7", 400
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET não configurados.", 400

    import urllib.parse, hashlib, base64, secrets
    code_verifier  = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b'=').decode()
    state = f"{secrets.token_urlsafe(16)}.{ga_id}"

    # Salva o code_verifier no JSON temporário (sem depender de sessão/cookie)
    tokens = _load_tokens()
    tokens[f"__pkce_{state}"] = code_verifier
    _save_tokens(tokens)

    params = urllib.parse.urlencode({
        'client_id'            : GOOGLE_CLIENT_ID,
        'redirect_uri'         : GOOGLE_REDIRECT_URI,
        'response_type'        : 'code',
        'scope'                : ' '.join(SCOPES_METRICAS),
        'access_type'          : 'offline',
        'prompt'               : 'consent',
        'state'                : state,
        'code_challenge'       : code_challenge,
        'code_challenge_method': 'S256',
    })
    return redirect('https://accounts.google.com/o/oauth2/v2/auth?' + params)


@app.route('/api/metricas/oauth/callback')
def metricas_oauth_callback():
    """Callback do Google — salva o token associado ao GA ID."""
    state = request.args.get('state', '')
    code  = request.args.get('code', '')

    if not state or not code:
        return "Parâmetros ausentes no callback.", 400
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        return "Erro de configuração OAuth.", 400

    # Recupera ga_id e code_verifier do state e do JSON temporário
    ga_id = state.split('.')[-1] if '.' in state else ''
    if not ga_id or not ga_id.startswith('G-'):
        return "State inválido.", 400

    tokens        = _load_tokens()
    code_verifier = tokens.pop(f"__pkce_{state}", None)
    _save_tokens(tokens)

    if not code_verifier:
        return "Code verifier não encontrado. Tente novamente.", 400

    try:
        import urllib.parse
        import requests as _req2
        resp = _req2.post('https://oauth2.googleapis.com/token', data={
            'client_id'    : GOOGLE_CLIENT_ID,
            'client_secret': GOOGLE_CLIENT_SECRET,
            'code'         : code,
            'code_verifier': code_verifier,
            'grant_type'   : 'authorization_code',
            'redirect_uri' : GOOGLE_REDIRECT_URI,
        })
        tok_data = resp.json()
        if 'error' in tok_data:
            return f"Erro OAuth: {tok_data}", 400

        from google.oauth2.credentials import Credentials as GCreds
        creds = GCreds(
            token         = tok_data.get('access_token'),
            refresh_token = tok_data.get('refresh_token'),
            token_uri     = 'https://oauth2.googleapis.com/token',
            client_id     = GOOGLE_CLIENT_ID,
            client_secret = GOOGLE_CLIENT_SECRET,
            scopes        = SCOPES_METRICAS,
        )

        # Coleta todas as properties e streams da conta
        all_properties = []
        ga4_property_auto = ""
        try:
            svc_admin = build('analyticsadmin', 'v1beta', credentials=creds, cache_discovery=False)
            props = svc_admin.properties().list(filter="parent:accounts/-").execute()
            ga_id_num = ga_id.replace('G-', '').upper()
            for p in props.get('properties', []):
                prop_name    = p.get('name', '')
                prop_display = p.get('displayName', prop_name)
                stream_url   = ''
                measurement_id = ''
                try:
                    streams = svc_admin.properties().dataStreams().list(parent=prop_name).execute()
                    for s in streams.get('dataStreams', []):
                        mid = s.get('webStreamData', {}).get('measurementId', '')
                        url = s.get('webStreamData', {}).get('defaultUri', '')
                        if mid:
                            measurement_id = mid
                            stream_url     = url
                            if mid.replace('G-', '').upper() == ga_id_num:
                                ga4_property_auto = prop_name
                            break
                except Exception:
                    pass
                all_properties.append({
                    'name'          : prop_name,
                    'displayName'   : prop_display,
                    'measurementId' : measurement_id,
                    'streamUrl'     : stream_url,
                })
        except Exception:
            pass

        # Coleta sites do Search Console
        gsc_sites = []
        try:
            svc_sc  = build('searchconsole', 'v1', credentials=creds, cache_discovery=False)
            sites   = svc_sc.sites().list().execute()
            gsc_sites = [e.get('siteUrl', '') for e in sites.get('siteEntry', []) if e.get('siteUrl')]
        except Exception:
            pass

        # Salva token + listas temporárias indexado pelo GA ID
        tokens = _load_tokens()
        tokens[ga_id] = {
            'token'           : creds.token,
            'refresh_token'   : creds.refresh_token,
            'gsc_site'        : '',
            'ga4_property'    : ga4_property_auto,
            'all_properties'  : all_properties,
            'gsc_sites'       : gsc_sites,
        }
        _save_tokens(tokens)

        # Se achou a property pelo GA ID e só tem 1 site GSC, salva direto sem precisar de seleção
        gsc_auto = ''
        if gsc_sites:
            if len(gsc_sites) == 1:
                gsc_auto = gsc_sites[0]
            elif ga4_property_auto:
                # Tenta casar pelo domínio do stream
                try:
                    from urllib.parse import urlparse
                    for prop in all_properties:
                        if prop['name'] == ga4_property_auto and prop['streamUrl']:
                            domain = urlparse(prop['streamUrl']).netloc.replace('www.', '')
                            for s in gsc_sites:
                                if domain in s:
                                    gsc_auto = s
                                    break
                except Exception:
                    pass

        if gsc_auto:
            tokens[ga_id]['gsc_site'] = gsc_auto
            _save_tokens(tokens)

        # Se achou tudo automaticamente, vai direto pro painel
        if ga4_property_auto and gsc_auto:
            return redirect(f'/metricas?id={ga_id}&conectado=1')

        # Caso contrário, redireciona pra tela de seleção
        return redirect(f'/metricas?id={ga_id}&selecionar=1')

    except Exception as e:
        return f"Erro no callback OAuth: {e}", 400


@app.route('/api/metricas/gsc')
def metricas_gsc():
    """Retorna dados reais do Search Console para o GA ID informado."""
    ga_id = request.args.get('ga_id', '').upper().strip()
    dias  = int(request.args.get('dias', 28))

    if not ga_id:
        return jsonify({"success": False, "error": "ga_id não informado"}), 400

    creds = _get_creds(ga_id)
    if not creds or not creds.valid:
        return jsonify({"success": False, "error": "Search Console não conectado. Faça login com o Google."}), 401

    tokens   = _load_tokens()
    gsc_site = tokens.get(ga_id, {}).get('gsc_site', '')
    if not gsc_site:
        return jsonify({"success": False, "error": "Nenhum site encontrado no Search Console."}), 400

    try:
        svc   = build('searchconsole', 'v1', credentials=creds, cache_discovery=False)
        end   = _dt.date.today() - _dt.timedelta(days=3)
        start = end - _dt.timedelta(days=dias)

        # Totais por dia
        resp_dia = svc.searchanalytics().query(siteUrl=gsc_site, body={
            'startDate' : start.strftime('%Y-%m-%d'),
            'endDate'   : end.strftime('%Y-%m-%d'),
            'dimensions': ['date'],
            'rowLimit'  : 90,
        }).execute()

        por_dia = []
        total_cliques = total_impressoes = total_ctr_sum = total_pos_sum = 0
        rows = resp_dia.get('rows', [])
        for row in rows:
            cl = row.get('clicks', 0)
            im = row.get('impressions', 0)
            ct = round(row.get('ctr', 0) * 100, 2)
            po = round(row.get('position', 0), 1)
            por_dia.append({'data': row['keys'][0], 'cliques': cl, 'impressoes': im, 'ctr': ct, 'posicao': po})
            total_cliques    += cl
            total_impressoes += im
            total_ctr_sum    += ct
            total_pos_sum    += po

        n = len(rows) or 1
        totais = {
            'cliques'   : total_cliques,
            'impressoes': total_impressoes,
            'ctr'       : round(total_ctr_sum / n, 1),
            'posicao'   : round(total_pos_sum / n, 1),
        }

        # Top páginas
        resp_pg = svc.searchanalytics().query(siteUrl=gsc_site, body={
            'startDate' : start.strftime('%Y-%m-%d'),
            'endDate'   : end.strftime('%Y-%m-%d'),
            'dimensions': ['page'],
            'rowLimit'  : 10,
            'orderBy'   : [{'fieldName': 'clicks', 'sortOrder': 'DESCENDING'}],
        }).execute()
        top_paginas = [{
            'pagina'    : r['keys'][0],
            'cliques'   : r.get('clicks', 0),
            'impressoes': r.get('impressions', 0),
            'ctr'       : round(r.get('ctr', 0) * 100, 2),
            'posicao'   : round(r.get('position', 0), 1),
        } for r in resp_pg.get('rows', [])]

        # Top keywords
        resp_kw = svc.searchanalytics().query(siteUrl=gsc_site, body={
            'startDate' : start.strftime('%Y-%m-%d'),
            'endDate'   : end.strftime('%Y-%m-%d'),
            'dimensions': ['query'],
            'rowLimit'  : 10,
            'orderBy'   : [{'fieldName': 'clicks', 'sortOrder': 'DESCENDING'}],
        }).execute()
        top_keywords = [{
            'query'     : r['keys'][0],
            'cliques'   : r.get('clicks', 0),
            'impressoes': r.get('impressions', 0),
            'ctr'       : round(r.get('ctr', 0) * 100, 2),
            'posicao'   : round(r.get('position', 0), 1),
        } for r in resp_kw.get('rows', [])]

        # Por dispositivo
        resp_dev = svc.searchanalytics().query(siteUrl=gsc_site, body={
            'startDate' : start.strftime('%Y-%m-%d'),
            'endDate'   : end.strftime('%Y-%m-%d'),
            'dimensions': ['device'],
            'rowLimit'  : 10,
        }).execute()
        por_device = [{
            'device'    : r['keys'][0].upper(),
            'cliques'   : r.get('clicks', 0),
            'impressoes': r.get('impressions', 0),
            'ctr'       : round(r.get('ctr', 0) * 100, 2),
            'posicao'   : round(r.get('position', 0), 1),
        } for r in resp_dev.get('rows', [])]

        # Por país
        resp_pais = svc.searchanalytics().query(siteUrl=gsc_site, body={
            'startDate' : start.strftime('%Y-%m-%d'),
            'endDate'   : end.strftime('%Y-%m-%d'),
            'dimensions': ['country'],
            'rowLimit'  : 10,
            'orderBy'   : [{'fieldName': 'clicks', 'sortOrder': 'DESCENDING'}],
        }).execute()
        por_pais = [{
            'pais'      : r['keys'][0],
            'cliques'   : r.get('clicks', 0),
            'impressoes': r.get('impressions', 0),
        } for r in resp_pais.get('rows', [])]

        return jsonify({
            "success"     : True,
            # totais flat (compatível com o frontend que lê dGSC.cliques direto)
            "cliques"     : totais['cliques'],
            "impressoes"  : totais['impressoes'],
            "ctr"         : totais['ctr'],
            "posicao"     : totais['posicao'],
            "totais"      : totais,
            "por_dia"     : por_dia,
            "por_pagina"  : top_paginas,
            "top_paginas" : top_paginas,
            "top_keywords": top_keywords,
            "por_device"  : por_device,
            "por_pais"    : por_pais,
            "site"        : gsc_site,
            "periodo_dias": dias,
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/metricas/ga4')
def metricas_ga4():
    """Retorna dados reais do GA4 para o GA ID informado."""
    ga_id = request.args.get('ga_id', '').upper().strip()
    dias  = int(request.args.get('dias', 28))

    if not ga_id:
        return jsonify({"success": False, "error": "ga_id não informado"}), 400

    creds = _get_creds(ga_id)
    if not creds or not creds.valid:
        return jsonify({"success": False, "error": "Google Analytics não conectado."}), 401

    tokens      = _load_tokens()
    ga4_property = tokens.get(ga_id, {}).get('ga4_property', '')

    if not ga4_property:
        ga4_property = request.args.get('property', '')
    if not ga4_property:
        return jsonify({"success": False, "error": "Property GA4 não encontrada. Informe ?property=properties/XXXXXXXXX"}), 400

    try:
        svc   = build('analyticsdata', 'v1beta', credentials=creds, cache_discovery=False)
        end   = _dt.date.today() - _dt.timedelta(days=1)
        start = end - _dt.timedelta(days=dias)

        # Sessões por dia
        resp_dia = svc.properties().runReport(property=ga4_property, body={
            'dateRanges': [{'startDate': start.strftime('%Y-%m-%d'), 'endDate': end.strftime('%Y-%m-%d')}],
            'dimensions': [{'name': 'date'}],
            'metrics'   : [{'name': 'sessions'}, {'name': 'activeUsers'}, {'name': 'screenPageViews'}],
            'orderBys'  : [{'dimension': {'dimensionName': 'date'}}],
        }).execute()

        por_dia = []
        for row in resp_dia.get('rows', []):
            dt = row['dimensionValues'][0]['value']
            por_dia.append({
                'data'     : f"{dt[:4]}-{dt[4:6]}-{dt[6:]}",
                'sessoes'  : int(row['metricValues'][0]['value']),
                'usuarios' : int(row['metricValues'][1]['value']),
                'pageviews': int(row['metricValues'][2]['value']),
            })

        # Totais gerais
        resp_tot = svc.properties().runReport(property=ga4_property, body={
            'dateRanges': [{'startDate': start.strftime('%Y-%m-%d'), 'endDate': end.strftime('%Y-%m-%d')}],
            'metrics'   : [
                {'name': 'sessions'},
                {'name': 'activeUsers'},
                {'name': 'screenPageViews'},
                {'name': 'averageSessionDuration'},
            ],
        }).execute()

        totais = {'sessoes': 0, 'usuarios': 0, 'pageviews': 0, 'tempo_medio': 0}
        if resp_tot.get('rows'):
            mv = resp_tot['rows'][0]['metricValues']
            totais = {
                'sessoes'    : int(mv[0]['value']),
                'usuarios'   : int(mv[1]['value']),
                'pageviews'  : int(mv[2]['value']),
                'tempo_medio': round(float(mv[3]['value']), 0),
            }

        # Canais de tráfego
        resp_cn = svc.properties().runReport(property=ga4_property, body={
            'dateRanges': [{'startDate': start.strftime('%Y-%m-%d'), 'endDate': end.strftime('%Y-%m-%d')}],
            'dimensions': [{'name': 'sessionDefaultChannelGroup'}],
            'metrics'   : [{'name': 'sessions'}, {'name': 'activeUsers'}],
            'orderBys'  : [{'metric': {'metricName': 'sessions'}, 'desc': True}],
            'limit'     : 8,
        }).execute()

        canais = [{
            'canal'   : r['dimensionValues'][0]['value'],
            'sessoes' : int(r['metricValues'][0]['value']),
            'usuarios': int(r['metricValues'][1]['value']),
        } for r in resp_cn.get('rows', [])]

        # Top páginas
        resp_pg = svc.properties().runReport(property=ga4_property, body={
            'dateRanges': [{'startDate': start.strftime('%Y-%m-%d'), 'endDate': end.strftime('%Y-%m-%d')}],
            'dimensions': [{'name': 'pagePath'}],
            'metrics'   : [{'name': 'screenPageViews'}, {'name': 'activeUsers'}],
            'orderBys'  : [{'metric': {'metricName': 'screenPageViews'}, 'desc': True}],
            'limit'     : 10,
        }).execute()

        top_paginas = [{
            'pagina'   : r['dimensionValues'][0]['value'],
            'pageviews': int(r['metricValues'][0]['value']),
            'usuarios' : int(r['metricValues'][1]['value']),
        } for r in resp_pg.get('rows', [])]

        return jsonify({
            "success"     : True,
            "totais"      : totais,
            "por_dia"     : por_dia,
            "canais"      : canais,
            "top_paginas" : top_paginas,
            "periodo_dias": dias,
        })

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/metricas/ia-analise', methods=['POST'])
def metricas_ia_analise():
    """Gera análise IA com Groq a partir dos dados GSC + GA4."""
    data    = request.json or {}
    gsc     = data.get('gsc', {})
    ga4     = data.get('ga4', {})
    periodo = data.get('periodo', 28)

    partes = []
    if gsc:
        partes.append(
            f"Search Console ({periodo} dias): {gsc.get('cliques', 0)} cliques, "
            f"{gsc.get('impressoes', 0)} impressões, CTR {gsc.get('ctr', 0)}%, "
            f"posição média {gsc.get('posicao', 0)}."
        )
    if ga4:
        seg = ga4.get('tempo_medio', 0)
        partes.append(
            f"Analytics ({periodo} dias): {ga4.get('sessoes', 0)} sessões, "
            f"{ga4.get('usuarios', 0)} usuários, {ga4.get('pageviews', 0)} visualizações, "
            f"tempo médio {int(seg // 60)}m{int(seg % 60)}s."
        )

    if not partes:
        return jsonify({"success": False, "error": "Sem dados para analisar."}), 400

    prompt = (
        "Você é um especialista em marketing digital e SEO. "
        "Analise os dados abaixo e dê um diagnóstico direto e acionável em 3-4 frases. "
        "Aponte o ponto mais crítico, o que está bom e 1 ação concreta para melhorar. "
        "Seja objetivo, sem jargões desnecessários.\n\n"
        "Dados: " + " ".join(partes)
    )

    try:
        import requests as _req
        resp = _req.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_KEY}",
                "Content-Type" : "application/json",
            },
            json={
                "model"      : "llama-3.3-70b-versatile",
                "temperature": 0.5,
                "max_tokens" : 300,
                "messages"   : [
                    {"role": "system", "content": "Você é um analista de marketing digital direto e prático. Responda em português do Brasil."},
                    {"role": "user",   "content": prompt},
                ],
            },
            timeout=20,
        )
        analise = resp.json()['choices'][0]['message']['content'].strip()
        return jsonify({"success": True, "analise": analise})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# ═══════════════════════════════════════════════════════════════════
# FIM DO BLOCO DE MÉTRICAS
# ═══════════════════════════════════════════════════════════════════

# ─── RUN ──────────────────────────────────────────────────────────────────────

# ─── SALVAR SELEÇÃO DE PROPERTY/SITE ─────────────────────────────────────────
@app.route('/api/metricas/selecionar', methods=['POST'])
def metricas_selecionar():
    """Salva a property GA4 e site GSC escolhidos pelo usuário."""
    data         = request.json or {}
    ga_id        = data.get('ga_id', '').upper().strip()
    ga4_property = data.get('ga4_property', '').strip()
    gsc_site     = data.get('gsc_site', '').strip()

    if not ga_id:
        return jsonify({"success": False, "error": "ga_id não informado"}), 400

    tokens = _load_tokens()
    if ga_id not in tokens:
        return jsonify({"success": False, "error": "Token não encontrado. Faça o OAuth novamente."}), 400

    tokens[ga_id]['ga4_property'] = ga4_property
    tokens[ga_id]['gsc_site']     = gsc_site
    _save_tokens(tokens)
    return jsonify({"success": True})

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
