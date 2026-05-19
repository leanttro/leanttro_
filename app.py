import os
import jwt
import bcrypt
import mercadopago
import psycopg2
import psycopg2.extras
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# ─── CONFIG VIA ENV ───────────────────────────────────────────────────────────
DB_URL       = os.environ["DATABASE_URL"]        # postgresql://user:pass@host:port/db
JWT_SECRET   = os.environ["JWT_SECRET"]          # string aleatória segura
MP_TOKEN     = os.environ["MP_ACCESS_TOKEN"]     # Access Token do Mercado Pago

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

# ─── PRODUTOS ─────────────────────────────────────────────────────────────────
@app.route("/produtos", methods=["GET"])
def listar_produtos():
    categoria = request.args.get("categoria")
    conn = get_db(); cur = conn.cursor()
    if categoria:
        cur.execute("SELECT * FROM produtos WHERE ativo = true AND categoria = %s ORDER BY destaque DESC, id DESC", (categoria,))
    else:
        cur.execute("SELECT * FROM produtos WHERE ativo = true ORDER BY destaque DESC, id DESC")
    produtos = cur.fetchall()
    cur.close(); conn.close()
    return jsonify(list(produtos))

@app.route("/produtos/<slug>", methods=["GET"])
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
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO produtos (nome, slug, descricao, categoria, preco, arquivo_url, imagem_url, ativo, destaque)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (d["nome"], d["slug"], d.get("descricao"), d.get("categoria"), d["preco"],
          d.get("arquivo_url"), d.get("imagem_url"), d.get("ativo", True), d.get("destaque", False)))
    novo_id = cur.fetchone()["id"]
    conn.commit(); cur.close(); conn.close()
    return jsonify({"id": novo_id}), 201

@app.route("/admin/produtos/<int:id>", methods=["PUT"])
@token_required
def editar_produto(id):
    d = request.json
    conn = get_db(); cur = conn.cursor()
    cur.execute("""
        UPDATE produtos SET nome=%s, slug=%s, descricao=%s, categoria=%s, preco=%s,
        arquivo_url=%s, imagem_url=%s, ativo=%s, destaque=%s, updated_em=now()
        WHERE id=%s
    """, (d["nome"], d["slug"], d.get("descricao"), d.get("categoria"), d["preco"],
          d.get("arquivo_url"), d.get("imagem_url"), d.get("ativo", True), d.get("destaque", False), id))
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

# ─── HUB DO JABAQUARA ─────────────────────────────────────────────────────────
@app.route("/hub/categorias", methods=["GET"])
def listar_hub_categorias():
    conn = get_db(); cur = conn.cursor()
    cur.execute("SELECT * FROM hub_categorias ORDER BY nome")
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
    bairro = request.args.get("bairro")
    conn = get_db(); cur = conn.cursor()
    query = """
        SELECT n.*, c.nome as categoria_nome FROM hub_negocios n
        JOIN hub_categorias c ON c.id = n.categoria_id
        WHERE n.ativo = true
    """
    params = []
    if categoria:
        query += " AND c.slug = %s"
        params.append(categoria)
    if bairro:
        query += " AND n.bairro ILIKE %s"
        params.append(f"%{bairro}%")
    query += " ORDER BY n.destaque DESC, n.visualizacoes DESC"
    cur.execute(query, params)
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

@app.route("/blog", methods=["GET"])
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

@app.route("/blog/<slug>", methods=["GET"])
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
