"""
SEO programático da Leanttro — páginas "{oferta} para {categoria} em São Paulo".

Como funciona
-------------
1. Você cadastra os bairros (seo_bairros) — pode colar os 1000+ de uma vez.
2. Pra cada categoria do hub (hub_categorias) x oferta (site / automação / plataforma),
   o sistema monta um RASCUNHO escolhendo variantes de texto (templates) e bairros
   a citar, sempre priorizando o que menos foi usado até agora.
3. Você revisa, escreve o parágrafo "nota do autor" (a parte que só você sabe),
   sobe uma foto e publica.
4. Só entra no sitemap o que está publicado. Publicar passa por uma trava de
   qualidade (tamanho mínimo, foto, bairros, similaridade com outras páginas).

As páginas são renderizadas NO SERVIDOR (Jinja), com title/canonical/JSON-LD já no
HTML — diferente de bairro.html/categoria.html, que montam tudo via JS.

Integração (3 linhas no app.py) — veja LEIA-ME-SEO.md.
"""
import json
import random
import re
import unicodedata
from datetime import datetime

from flask import abort, jsonify, render_template, request, Response, redirect

# ─────────────────────────────────────────────────────────────────────────────
# OFERTAS
# ─────────────────────────────────────────────────────────────────────────────
# ATENÇÃO: confira estes textos — são afirmações comerciais suas. Os preços de
# "site" vieram do JSON-LD do sites.html; automação/plataforma estão "sob orçamento".
OFERTAS = {
    "site": {
        "slug": "site",
        "nome": "Criação de site",
        "curto": "site",
        "cta_wa": "orçamento de site",
        "entregas": [
            "Landing page ou site institucional pensado para o seu segmento",
            "Botão de WhatsApp e formulário de contato com aviso por e-mail",
            "SEO básico: título, descrição, velocidade e dados estruturados",
            "Painel para você ver os contatos que chegam",
            "Contrato e nota fiscal em todos os projetos",
            "Pagamento em duas partes: 50% na entrada e 50% só depois de aprovar",
            "Pixel e Analytics instalados para você medir o que dá resultado",
            "Hospedagem e pequenas alterações no plano mensal",
        ],
        "preco": "Landing page a partir de R$ 700 e site profissional a partir de R$ 1.700.",
        "servico_ld": "Criação de sites e landing pages",
    },
    "automacao": {
        "slug": "automacao",
        "nome": "Automação de processos",
        "curto": "automação",
        "cta_wa": "orçamento de automação",
        "entregas": [
            "Mapeamento do que hoje é feito na mão e se repete todo dia",
            "Atendimento e respostas automáticas no WhatsApp",
            "Integração entre planilhas, formulários, agenda e cobrança",
            "Avisos automáticos de agendamento, pagamento e retorno de cliente",
            "Relatórios que chegam sozinhos, sem ninguém montar planilha",
            "Passo a passo documentado para sua equipe saber o que roda",
            "Ajustes combinados antes de qualquer cobrança extra",
        ],
        "preco": "Valor sob orçamento, conforme o processo a automatizar.",
        "servico_ld": "Automação de processos para pequenas empresas",
    },
    "plataforma": {
        "slug": "plataforma",
        "nome": "Desenvolvimento de plataforma",
        "curto": "plataforma",
        "cta_wa": "orçamento de plataforma",
        "entregas": [
            "Plataforma web sob medida: portal, marketplace, catálogo ou classificados",
            "Painel administrativo para você controlar tudo sem depender de ninguém",
            "Cadastro, login e áreas separadas por tipo de usuário",
            "Busca, filtros e páginas otimizadas para aparecer no Google",
            "Pagamentos online quando o modelo de negócio pede",
            "Código e acesso ao banco de dados documentados",
            "Evolução por etapas: entra no ar o essencial e cresce depois",
        ],
        "preco": "Sistemas sob medida a partir de R$ 3.000, valor final sob orçamento.",
        "servico_ld": "Desenvolvimento de plataformas web sob medida",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# BIBLIOTECA DE TEMPLATES (slots → lista de variantes)
# Placeholders: {cat} {cat_l} {local} {oferta} {oferta_l} {bairros_lista} {n_bairros}
# Pra adicionar variantes, é só acrescentar um texto na lista. O índice é salvo
# na página, então NÃO reordene/remova as existentes (só acrescente no fim).
# ─────────────────────────────────────────────────────────────────────────────
TEMPLATES = {
    "intro": [
        "Quem busca {cat_l} em {local} compara no celular antes de decidir — e quem aparece primeiro leva o contato. {oferta} é o que coloca o seu negócio nessa disputa, com um resultado que dá pra medir.",
        "Se você tem um negócio de {cat_l} em {local}, provavelmente já perdeu cliente para um concorrente que simplesmente era mais fácil de achar. {oferta} resolve isso sem depender de anúncio pago.",
        "{cat} em {local} disputam a mesma atenção: a de quem está com o celular na mão procurando um lugar agora. Aqui você vê como {oferta_l} ajuda o seu negócio a ser encontrado e a fechar mais contatos.",
        "Presença digital para {cat_l} em {local} não precisa ser cara nem complicada. O que importa é ser encontrado, passar confiança em poucos segundos e facilitar o contato. É para isso que serve {oferta_l}.",
        "Cada negócio de {cat_l} tem uma rotina própria, e {oferta_l} só funciona quando respeita isso. Esta página explica como trabalho com {cat_l} em {local}, o que entrego e quanto costuma custar.",
    ],
    "problema_titulo": [
        "Por que {cat_l} em {local} perdem clientes online",
        "O que costuma travar o crescimento de {cat_l} em {local}",
        "Os problemas mais comuns de quem tem {cat_l} em {local}",
        "Onde {cat_l} de {local} deixam dinheiro na mesa",
    ],
    "problema_texto": [
        [
            "A maior parte do público de {cat_l} em {local} começa a busca no Google ou no Maps. Se o seu negócio não aparece, ou aparece com informação velha, o cliente fecha com quem está mais à mão.",
            "O segundo problema é o contato: telefone errado, horário desatualizado e nenhum botão de WhatsApp. Cada passo a mais é um cliente que desiste.",
        ],
        [
            "Muitos negócios de {cat_l} dependem só de indicação e rede social. Funciona até certo ponto, mas o alcance some quando você para de postar e você não é dono do canal.",
            "Um endereço próprio na internet continua aparecendo para quem pesquisa, mesmo quando você está ocupado atendendo.",
        ],
        [
            "Em {local}, a concorrência entre {cat_l} é grande e o cliente compara em segundos. Página lenta, fotos ruins ou falta de preço claro fazem a pessoa voltar para a busca.",
            "O ponto de partida é simples: informação clara, carregamento rápido e um caminho direto até o seu WhatsApp.",
        ],
        [
            "Boa parte do trabalho de {cat_l} é repetitiva: responder as mesmas perguntas, confirmar horários, cobrar, avisar. Isso consome horas que poderiam virar atendimento ou venda.",
            "Quando o básico está organizado e ligado entre si, o dono para de apagar incêndio e passa a decidir com dados.",
        ],
    ],
    "entregas_titulo": [
        "O que entrego para {cat_l}",
        "O que está incluído para o seu negócio de {cat_l}",
        "Como funciona o trabalho com {cat_l}",
    ],
    "bairros_titulo": [
        "Atendo {cat_l} em {local} e arredores",
        "Bairros de São Paulo onde atendo {oferta_l}",
        "{cat} perto de você: bairros que atendemos",
        "De {local} a toda a cidade: onde atuamos",
    ],
    "bairros_texto": [
        "O trabalho é feito online, então o atendimento vale para toda a cidade. Entre os bairros que atendo estão {bairros_lista} e as regiões vizinhas, e conhecer o perfil de cada região ajuda a escrever para o público certo.",
        "Cada região de São Paulo tem um perfil de cliente diferente. Por isso a página do seu negócio cita o bairro onde você atua. Entre os endereços que atendo estão {bairros_lista}.",
        "Se o seu negócio fica em {bairros_lista} ou por perto, o contato é direto e o projeto leva em conta o público de cada região.",
        "Trabalho em São Paulo e levo em conta a dinâmica de cada região. Para {cat_l}, isso vale em bairros como {bairros_lista}.",
    ],
    "cta": [
        ("Vamos tirar o seu negócio do papel?", "Me chame no WhatsApp, conte o que você faz e receba uma proposta com preço e prazo antes de pagar qualquer coisa."),
        ("Quer ver como ficaria para o seu negócio?", "Fale comigo: você conta o que precisa e eu respondo com escopo, prazo e valor. Sem compromisso."),
        ("Peça seu orçamento hoje", "Resposta direta pelo WhatsApp. Contrato, nota fiscal e 50% só depois de aprovar o resultado."),
    ],
    "faq": [
        [
            ("Quanto custa {oferta_l} para {cat_l}?", "{preco} O valor final depende do que o seu negócio precisa; você recebe a proposta fechada antes de pagar."),
            ("Quanto tempo leva?", "Projetos simples ficam prontos em poucos dias depois que eu recebo o material. Os maiores têm prazo combinado na proposta, antes da entrada."),
            ("Vocês atendem {local}?", "Sim. O trabalho é online e atendo {local} e toda a cidade de São Paulo."),
            ("Preciso entender de tecnologia?", "Não. Você me conta como o negócio funciona e eu cuido da parte técnica, explicando tudo sem jargão."),
        ],
        [
            ("{oferta} realmente traz cliente para {cat_l}?", "Traz quando é bem feito: informação clara, velocidade, contato fácil e otimização para as buscas que o seu cliente faz. Nenhum resultado é prometido, mas tudo é medido."),
            ("Como é o pagamento?", "Você paga 50% de entrada depois de combinarmos o escopo e os outros 50% só depois de aprovar. Há contrato e nota fiscal."),
            ("E depois que estiver no ar?", "Pequenas mudanças como textos, fotos, preços e telefones entram no plano mensal. Coisas novas viram orçamento."),
            ("Posso começar pequeno?", "Pode. O melhor caminho costuma ser colocar o essencial no ar e evoluir conforme o retorno aparece."),
        ],
        [
            ("Por que contratar alguém de {local} e não uma agência grande?", "Atendimento direto com quem faz o trabalho, sem intermediários, e preço compatível com pequenos negócios."),
            ("O que preciso enviar para começar?", "Logo, fotos e informações que você já tiver. Textos longos, criação de fotos e logos são orçados à parte, sempre com seu de acordo."),
            ("Como sei que deu certo?", "Você acompanha os contatos que chegam, as visitas e as buscas que levam ao seu negócio, com números reais."),
            ("{oferta} serve para {cat_l} de qualquer tamanho?", "Serve. O escopo muda: de uma página simples até um sistema completo, de acordo com o seu momento."),
        ],
    ],
}

SLOTS = ["intro", "problema_titulo", "problema_texto", "entregas_titulo",
         "bairros_titulo", "bairros_texto", "cta", "faq"]

# ─────────────────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────────────────
def slugify(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _lista(nomes, n=4):
    nomes = list(nomes)[:n]
    if not nomes:
        return "toda a cidade"
    if len(nomes) == 1:
        return nomes[0]
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


def _fmt(txt, ctx):
    try:
        return txt.format(**ctx)
    except (KeyError, IndexError):
        return txt


def _cap(s):
    return s[:1].upper() + s[1:] if s else s


def _ctx(oferta_key, cat_nome, local, bairros_nomes):
    o = OFERTAS[oferta_key]
    return {
        "cat": cat_nome,
        "cat_l": cat_nome.lower(),
        "local": local,
        "oferta": o["nome"],
        "oferta_l": o["nome"].lower(),
        "bairros_lista": _lista(bairros_nomes, 4),
        "n_bairros": len(bairros_nomes),
        "preco": o["preco"],
    }


def montar_conteudo(oferta_key, cat_nome, local, bairros_nomes, variantes, nota_autor=""):
    """Transforma (oferta, categoria, local, bairros, índices de variantes) em conteúdo estruturado."""
    o = OFERTAS[oferta_key]
    c = _ctx(oferta_key, cat_nome, local, bairros_nomes)
    v = {s: variantes.get(s, 0) % len(TEMPLATES[s]) for s in SLOTS}

    cta_t, cta_x = TEMPLATES["cta"][v["cta"]]
    faq = [{"q": _fmt(q, c), "a": _fmt(a, c)} for q, a in TEMPLATES["faq"][v["faq"]]]

    # Itens das entregas: 5 dos itens da oferta, rotacionados pela variante
    itens = o["entregas"][:]
    rot = v["intro"] % len(itens)
    itens = itens[rot:] + itens[:rot]

    return {
        "intro": _fmt(TEMPLATES["intro"][v["intro"]], c),
        "problema": {
            "h2": _cap(_fmt(TEMPLATES["problema_titulo"][v["problema_titulo"]], c)),
            "paragrafos": [_fmt(p, c) for p in TEMPLATES["problema_texto"][v["problema_texto"]]],
        },
        "entregas": {
            "h2": _cap(_fmt(TEMPLATES["entregas_titulo"][v["entregas_titulo"]], c)),
            "itens": itens[:6],
            "preco": o["preco"],
        },
        "bairros": {
            "h2": _cap(_fmt(TEMPLATES["bairros_titulo"][v["bairros_titulo"]], c)),
            "texto": _fmt(TEMPLATES["bairros_texto"][v["bairros_texto"]], c),
        },
        "nota_autor": nota_autor or "",
        "faq": faq,
        "cta": {"titulo": _fmt(cta_t, c), "texto": _fmt(cta_x, c)},
    }, v


def _texto_corrido(conteudo, h1="", bairros=None):
    """Todo o texto da página numa string (pra contar palavras e comparar similaridade)."""
    p = [h1, conteudo.get("intro", "")]
    pr = conteudo.get("problema", {})
    p += [pr.get("h2", "")] + pr.get("paragrafos", [])
    en = conteudo.get("entregas", {})
    p += [en.get("h2", "")] + en.get("itens", [])
    br = conteudo.get("bairros", {})
    p += [br.get("h2", ""), br.get("texto", "")]
    p.append(conteudo.get("nota_autor", ""))
    for f in conteudo.get("faq", []):
        p += [f["q"], f["a"]]
    return " ".join(x for x in p if x)


def _shingles(texto, n=5):
    w = re.findall(r"\w+", texto.lower())
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ─────────────────────────────────────────────────────────────────────────────
# REGISTRO NO FLASK
# ─────────────────────────────────────────────────────────────────────────────
def init_seo(app, get_db, token_required, base_url_fn):
    """Registra tabelas, rotas públicas e rotas de admin. Chame uma vez no app.py."""

    # ── Schema ──
    def _schema():
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS seo_bairros (
                    id SERIAL PRIMARY KEY,
                    nome TEXT NOT NULL,
                    slug TEXT UNIQUE NOT NULL,
                    zona TEXT DEFAULT '',
                    prioridade INT DEFAULT 0
                )""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS seo_paginas (
                    id SERIAL PRIMARY KEY,
                    slug TEXT UNIQUE NOT NULL,
                    oferta TEXT NOT NULL,
                    categoria_id INT,
                    categoria_nome TEXT NOT NULL,
                    bairro_slug TEXT,
                    h1 TEXT, titulo TEXT, meta_desc TEXT,
                    imagem_url TEXT, imagem_alt TEXT,
                    conteudo JSONB NOT NULL DEFAULT '{}',
                    bairros TEXT[] NOT NULL DEFAULT '{}',
                    variantes JSONB NOT NULL DEFAULT '{}',
                    publicada BOOLEAN NOT NULL DEFAULT false,
                    criada_em TIMESTAMP NOT NULL DEFAULT now(),
                    atualizada_em TIMESTAMP NOT NULL DEFAULT now(),
                    publicada_em TIMESTAMP
                )""")
            conn.commit(); cur.close(); conn.close()
        except Exception as e:  # não derruba o app se o banco estiver fora no boot
            print("[seo] schema:", e)

    _schema()

    # ── Consultas auxiliares ──
    def _bairros_com_pagina(cur):
        """slugs de bairros que já têm página /<bairro> (têm negócio cadastrado)."""
        cur.execute("SELECT DISTINCT bairro FROM hub_negocios WHERE ativo = true AND coalesce(bairro,'') <> ''")
        return {slugify(r["bairro"]) for r in cur.fetchall()}

    def _contagem_variantes(cur, oferta):
        """quantas vezes cada (slot, índice) já foi usado nessa oferta."""
        cur.execute("SELECT variantes FROM seo_paginas WHERE oferta = %s", (oferta,))
        cont = {}
        for r in cur.fetchall():
            for s, i in (r["variantes"] or {}).items():
                cont[(s, i)] = cont.get((s, i), 0) + 1
        return cont

    def _contagem_bairros(cur):
        cur.execute("SELECT b, count(*) AS n FROM seo_paginas, unnest(bairros) AS b GROUP BY b")
        return {r["b"]: r["n"] for r in cur.fetchall()}

    def _sortear_variantes(cont, rng):
        v = {}
        for s in SLOTS:
            n = len(TEMPLATES[s])
            menor = min(cont.get((s, i), 0) for i in range(n))
            candidatos = [i for i in range(n) if cont.get((s, i), 0) == menor]
            v[s] = rng.choice(candidatos)
        return v

    def _escolher_bairros(cur, rng, quantidade=12, foco_slug=None):
        """Prioriza bairros pouco citados (todo bairro recebe link de algum lugar).
        Em página bairro-específica, o bairro foco vem primeiro e depois os da mesma zona."""
        cur.execute("SELECT nome, slug, zona, prioridade FROM seo_bairros")
        todos = cur.fetchall()
        if not todos:
            return []
        cont = _contagem_bairros(cur)
        foco = next((b for b in todos if b["slug"] == foco_slug), None)
        pool = [b for b in todos if b["slug"] != foco_slug]
        if foco and foco["zona"]:
            mesma = [b for b in pool if b["zona"] == foco["zona"]]
            if len(mesma) >= 4:
                pool = mesma
        pool.sort(key=lambda b: (cont.get(b["nome"], 0) - b["prioridade"], rng.random()))
        escolhidos = [b["nome"] for b in pool[:quantidade - (1 if foco else 0)]]
        if foco:
            escolhidos.insert(0, foco["nome"])
        return escolhidos

    def _montar_rascunho(cur, oferta, cat, bairro_slug=None, variantes=None, bairros=None, seed=None):
        rng = random.Random(seed if seed is not None else random.randrange(1 << 30))
        foco = None
        if bairro_slug:
            cur.execute("SELECT nome, slug FROM seo_bairros WHERE slug = %s", (bairro_slug,))
            foco = cur.fetchone()
        local = foco["nome"] if foco else "São Paulo"
        cont = _contagem_variantes(cur, oferta)
        var = _sortear_variantes(cont, rng)
        if variantes:
            var.update({k: int(x) for k, x in variantes.items() if k in SLOTS})
        nomes = bairros if bairros is not None else _escolher_bairros(cur, rng, 12, bairro_slug)
        conteudo, var = montar_conteudo(oferta, cat["nome"], local, nomes, var)
        o = OFERTAS[oferta]
        if foco:
            h1 = f"{o['nome']} para {cat['nome']} em {foco['nome']}"
            slug = f"{o['slug']}-para-{slugify(cat['nome'])}-em-{foco['slug']}"
        else:
            h1 = f"{o['nome']} para {cat['nome']} em São Paulo"
            slug = f"{o['slug']}-para-{slugify(cat['nome'])}"
        return {
            "slug": slug, "oferta": oferta, "categoria_id": cat.get("id"),
            "categoria_nome": cat["nome"], "bairro_slug": bairro_slug or None,
            "h1": h1, "titulo": f"{h1} | Leanttro",
            "meta_desc": _recortar(conteudo["intro"], 155),
            "conteudo": conteudo, "bairros": nomes, "variantes": var,
        }

    def _recortar(txt, n):
        txt = re.sub(r"\s+", " ", txt).strip()
        if len(txt) <= n:
            return txt
        return txt[:n].rsplit(" ", 1)[0].rstrip(",;:.") + "…"

    # ── Trava de qualidade ──
    def _checar(cur, pg):
        erros, avisos = [], []
        conteudo = pg["conteudo"]
        if isinstance(conteudo, str):
            conteudo = json.loads(conteudo)
        texto = _texto_corrido(conteudo, pg.get("h1", ""))
        palavras = len(re.findall(r"\w+", texto))
        if palavras < 350:
            erros.append(f"Texto curto demais ({palavras} palavras; mínimo 350). Escreva a nota do autor.")
        if not pg.get("imagem_url"):
            erros.append("Falta a imagem.")
        elif not (pg.get("imagem_alt") or "").strip():
            avisos.append("Imagem sem texto alternativo (alt).")
        if len(pg.get("bairros") or []) < 6:
            erros.append("Cite pelo menos 6 bairros.")
        if len((conteudo.get("nota_autor") or "").split()) < 40:
            avisos.append("Nota do autor com menos de 40 palavras. É ela que torna a página única — cite um caso, um número, algo real do segmento.")
        if len(pg.get("meta_desc") or "") < 70:
            avisos.append("Meta description curta.")
        # similaridade com as demais
        meu = _shingles(texto)
        cur.execute("SELECT id, slug, h1, conteudo FROM seo_paginas WHERE id <> %s", (pg.get("id") or 0,))
        pior, pior_slug = 0.0, None
        for o in cur.fetchall():
            c2 = o["conteudo"] if isinstance(o["conteudo"], dict) else json.loads(o["conteudo"])
            sim = _jaccard(meu, _shingles(_texto_corrido(c2, o["h1"] or "")))
            if sim > pior:
                pior, pior_slug = sim, o["slug"]
        if pior >= 0.60:
            erros.append(f"{int(pior * 100)}% parecida com '{pior_slug}'. Troque variantes ou escreva mais texto próprio.")
        elif pior >= 0.45:
            avisos.append(f"{int(pior * 100)}% parecida com '{pior_slug}'. Dá pra publicar, mas vale diferenciar.")
        return {"ok": not erros, "erros": erros, "avisos": avisos,
                "palavras": palavras, "similaridade_max": round(pior, 2)}

    # ── Renderização pública ──
    def _montar_contexto_publico(cur, pg):
        base = base_url_fn()
        url = f"{base}/servicos/{pg['slug']}"
        com_pagina = _bairros_com_pagina(cur)
        chips = [{"nome": n, "slug": slugify(n), "link": slugify(n) in com_pagina} for n in (pg["bairros"] or [])]
        cur.execute("""SELECT slug, h1 FROM seo_paginas
                       WHERE publicada = true AND id <> %s AND oferta = %s
                       ORDER BY (categoria_id = %s) DESC, random() LIMIT 6""",
                    (pg["id"], pg["oferta"], pg.get("categoria_id") or 0))
        relacionadas = cur.fetchall()
        cur.execute("""SELECT slug, oferta FROM seo_paginas
                       WHERE publicada = true AND id <> %s AND categoria_id = %s AND oferta <> %s""",
                    (pg["id"], pg.get("categoria_id") or 0, pg["oferta"]))
        outras_ofertas = [{"slug": r["slug"], "nome": OFERTAS[r["oferta"]]["nome"]} for r in cur.fetchall()]
        o = OFERTAS[pg["oferta"]]
        wa = ("https://wa.me/5511913324827?text="
              + _urlenc(f"Olá! Vim pela página '{pg['h1']}' e quero um {o['cta_wa']}."))
        faq = pg["conteudo"].get("faq", [])
        ld = [
            {"@context": "https://schema.org", "@type": "Service", "@id": url + "#service",
             "name": pg["h1"], "serviceType": o["servico_ld"], "url": url,
             "provider": {"@type": "ProfessionalService", "name": "Leanttro", "url": base,
                          "telephone": "+55-11-91332-4827"},
             "areaServed": [{"@type": "City", "name": "São Paulo"}] +
                           [{"@type": "Place", "name": n} for n in (pg["bairros"] or [])[:6]],
             "description": pg["meta_desc"]},
            {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
                {"@type": "ListItem", "position": 1, "name": "Home", "item": base + "/"},
                {"@type": "ListItem", "position": 2, "name": "Serviços", "item": base + "/servicos"},
                {"@type": "ListItem", "position": 3, "name": pg["h1"], "item": url}]},
        ]
        if faq:
            ld.append({"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
                {"@type": "Question", "name": f["q"],
                 "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in faq]})
        return {"pg": pg, "c": pg["conteudo"], "url": url, "chips": chips, "wa": wa,
                "relacionadas": relacionadas, "outras_ofertas": outras_ofertas,
                "jsonld": json.dumps(ld, ensure_ascii=False).replace("</", "<\\/"), "base": base,
                "robots": "index, follow, max-snippet:-1, max-image-preview:large"
                          if pg["publicada"] else "noindex, nofollow"}

    def _urlenc(s):
        from urllib.parse import quote
        return quote(s, safe="")

    # ═════════════════════════ ROTAS PÚBLICAS ═════════════════════════
    @app.route("/servicos")
    def seo_hub():
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT slug, h1, oferta, categoria_nome FROM seo_paginas
                       WHERE publicada = true ORDER BY oferta, categoria_nome""")
        paginas = cur.fetchall()
        cur.close(); conn.close()
        grupos = {}
        for p in paginas:
            grupos.setdefault(p["oferta"], []).append(p)
        base = base_url_fn()
        return render_template("seo_hub.html", grupos=grupos, ofertas=OFERTAS, base=base,
                               total=len(paginas))

    @app.route("/servicos/<slug>")
    def seo_pagina(slug):
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM seo_paginas WHERE slug = %s AND publicada = true", (slug,))
        pg = cur.fetchone()
        if not pg:
            cur.close(); conn.close()
            abort(404)
        ctx = _montar_contexto_publico(cur, pg)
        cur.close(); conn.close()
        return render_template("seo_pagina.html", **ctx)

    # ═════════════════════════ ADMIN ═════════════════════════
    @app.route("/admin/seo")
    def seo_admin_page():
        return render_template("seo_admin.html")

    @app.route("/admin/seo/meta", methods=["GET"])
    @token_required
    def seo_meta():
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, nome, slug FROM hub_categorias WHERE ativo = true ORDER BY nome")
        cats = cur.fetchall()
        cur.execute("SELECT count(*) AS n FROM seo_bairros")
        nb = cur.fetchone()["n"]
        cur.close(); conn.close()
        return jsonify({
            "categorias": cats, "total_bairros": nb,
            "ofertas": {k: v["nome"] for k, v in OFERTAS.items()},
            "slots": {s: len(TEMPLATES[s]) for s in SLOTS},
        })

    @app.route("/admin/seo/bairros/importar", methods=["POST"])
    @token_required
    def seo_importar_bairros():
        """Corpo: {"texto": "Vila Mariana;Zona Sul\\nTatuapé;Zona Leste\\n..."}  (zona e prioridade opcionais)
        Linha: Nome[;Zona[;Prioridade]]"""
        texto = (request.json or {}).get("texto", "")
        novos = 0
        conn = get_db(); cur = conn.cursor()
        for linha in texto.splitlines():
            partes = [p.strip() for p in linha.split(";")]
            if not partes[0]:
                continue
            nome = partes[0]
            zona = partes[1] if len(partes) > 1 else ""
            try:
                prio = int(partes[2]) if len(partes) > 2 and partes[2] else 0
            except ValueError:
                prio = 0
            cur.execute("""INSERT INTO seo_bairros (nome, slug, zona, prioridade) VALUES (%s,%s,%s,%s)
                           ON CONFLICT (slug) DO UPDATE SET zona = EXCLUDED.zona, prioridade = EXCLUDED.prioridade""",
                        (nome, slugify(nome), zona, prio))
            novos += cur.rowcount
        conn.commit(); cur.close(); conn.close()
        return jsonify({"ok": True, "processados": novos})

    @app.route("/admin/seo/paginas", methods=["GET"])
    @token_required
    def seo_listar():
        conn = get_db(); cur = conn.cursor()
        cur.execute("""SELECT id, slug, h1, oferta, categoria_nome, bairro_slug, publicada,
                              imagem_url IS NOT NULL AND imagem_url <> '' AS tem_imagem, atualizada_em
                       FROM seo_paginas ORDER BY publicada DESC, oferta, categoria_nome""")
        rows = cur.fetchall()
        cur.close(); conn.close()
        return jsonify(rows)

    @app.route("/admin/seo/paginas/<int:id>", methods=["GET"])
    @token_required
    def seo_detalhe(id):
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM seo_paginas WHERE id = %s", (id,))
        pg = cur.fetchone()
        cur.close(); conn.close()
        return (jsonify(pg), 200) if pg else (jsonify({"erro": "não encontrada"}), 404)

    @app.route("/admin/seo/gerar", methods=["POST"])
    @token_required
    def seo_gerar():
        """Monta (sem salvar) um rascunho. Usado pelo botão 'sortear de novo' e ao trocar variantes."""
        d = request.json or {}
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, nome FROM hub_categorias WHERE id = %s", (d.get("categoria_id"),))
        cat = cur.fetchone()
        if not cat or d.get("oferta") not in OFERTAS:
            cur.close(); conn.close()
            return jsonify({"erro": "categoria ou oferta inválida"}), 400
        r = _montar_rascunho(cur, d["oferta"], cat, d.get("bairro_slug") or None,
                             d.get("variantes"), d.get("bairros"), d.get("seed"))
        cur.close(); conn.close()
        return jsonify(r)

    def _salvar(cur, d, id=None):
        conteudo = d["conteudo"]
        cols = (d["slug"], d["oferta"], d.get("categoria_id"), d["categoria_nome"], d.get("bairro_slug"),
                d.get("h1"), d.get("titulo"), d.get("meta_desc"), d.get("imagem_url"), d.get("imagem_alt"),
                json.dumps(conteudo, ensure_ascii=False), d.get("bairros") or [],
                json.dumps(d.get("variantes") or {}))
        if id:
            cur.execute("""UPDATE seo_paginas SET slug=%s, oferta=%s, categoria_id=%s, categoria_nome=%s,
                           bairro_slug=%s, h1=%s, titulo=%s, meta_desc=%s, imagem_url=%s, imagem_alt=%s,
                           conteudo=%s::jsonb, bairros=%s, variantes=%s::jsonb, atualizada_em=now()
                           WHERE id=%s RETURNING id""", cols + (id,))
        else:
            cur.execute("""INSERT INTO seo_paginas (slug, oferta, categoria_id, categoria_nome, bairro_slug,
                           h1, titulo, meta_desc, imagem_url, imagem_alt, conteudo, bairros, variantes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb) RETURNING id""", cols)
        return cur.fetchone()["id"]

    @app.route("/admin/seo/paginas", methods=["POST"])
    @token_required
    def seo_criar():
        d = request.json or {}
        conn = get_db(); cur = conn.cursor()
        try:
            novo = _salvar(cur, d)
            conn.commit()
        except Exception as e:
            conn.rollback(); cur.close(); conn.close()
            return jsonify({"erro": "Não foi possível salvar (slug repetido?)", "detalhe": str(e)}), 400
        cur.close(); conn.close()
        return jsonify({"id": novo}), 201

    @app.route("/admin/seo/paginas/<int:id>", methods=["PUT"])
    @token_required
    def seo_editar(id):
        d = request.json or {}
        conn = get_db(); cur = conn.cursor()
        try:
            _salvar(cur, d, id)
            conn.commit()
        except Exception as e:
            conn.rollback(); cur.close(); conn.close()
            return jsonify({"erro": "Não foi possível salvar", "detalhe": str(e)}), 400
        cur.close(); conn.close()
        return jsonify({"ok": True})

    @app.route("/admin/seo/paginas/<int:id>/checar", methods=["GET"])
    @token_required
    def seo_checar(id):
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM seo_paginas WHERE id = %s", (id,))
        pg = cur.fetchone()
        if not pg:
            cur.close(); conn.close()
            return jsonify({"erro": "não encontrada"}), 404
        r = _checar(cur, pg)
        cur.close(); conn.close()
        return jsonify(r)

    @app.route("/admin/seo/paginas/<int:id>/publicar", methods=["POST"])
    @token_required
    def seo_publicar(id):
        """Body: {"publicar": true|false, "forcar": false}. Despublicar nunca é bloqueado."""
        d = request.json or {}
        publicar = d.get("publicar", True)
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM seo_paginas WHERE id = %s", (id,))
        pg = cur.fetchone()
        if not pg:
            cur.close(); conn.close()
            return jsonify({"erro": "não encontrada"}), 404
        if publicar:
            chk = _checar(cur, pg)
            if not chk["ok"] and not d.get("forcar"):
                cur.close(); conn.close()
                return jsonify({"erro": "Reprovada na trava de qualidade", "checagem": chk}), 422
        cur.execute("""UPDATE seo_paginas SET publicada=%s,
                       publicada_em = CASE WHEN %s THEN coalesce(publicada_em, now()) ELSE publicada_em END
                       WHERE id=%s""", (publicar, publicar, id))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"ok": True, "publicada": publicar})

    @app.route("/admin/seo/paginas/<int:id>", methods=["DELETE"])
    @token_required
    def seo_deletar(id):
        conn = get_db(); cur = conn.cursor()
        cur.execute("DELETE FROM seo_paginas WHERE id = %s", (id,))
        conn.commit(); cur.close(); conn.close()
        return jsonify({"ok": True})

    @app.route("/admin/seo/paginas/<int:id>/preview", methods=["GET"])
    @token_required
    def seo_preview(id):
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT * FROM seo_paginas WHERE id = %s", (id,))
        pg = cur.fetchone()
        if not pg:
            cur.close(); conn.close()
            abort(404)
        ctx = _montar_contexto_publico(cur, pg)
        cur.close(); conn.close()
        return render_template("seo_pagina.html", **ctx)

    @app.route("/admin/seo/lote", methods=["POST"])
    @token_required
    def seo_lote():
        """Cria RASCUNHOS (nunca publica) pra toda categoria que ainda não tem página da oferta.
        Body: {"oferta": "site", "bairro_slug": null}"""
        d = request.json or {}
        oferta = d.get("oferta")
        if oferta not in OFERTAS:
            return jsonify({"erro": "oferta inválida"}), 400
        conn = get_db(); cur = conn.cursor()
        cur.execute("SELECT id, nome FROM hub_categorias WHERE ativo = true ORDER BY nome")
        cats = cur.fetchall()
        criadas, puladas = [], 0
        for cat in cats:
            r = _montar_rascunho(cur, oferta, cat, d.get("bairro_slug") or None)
            cur.execute("SELECT 1 FROM seo_paginas WHERE slug = %s", (r["slug"],))
            if cur.fetchone():
                puladas += 1
                continue
            _salvar(cur, r)
            conn.commit()  # commit a cada uma: a próxima enxerga as variantes/bairros já usados
            criadas.append(r["slug"])
        cur.close(); conn.close()
        return jsonify({"criadas": len(criadas), "puladas": puladas, "slugs": criadas})

    # ═════════════════════════ SITEMAP ═════════════════════════
    def sitemap_urls(base):
        """Devolve blocos <url> das páginas publicadas. Use no sitemap() do app.py."""
        out = []
        try:
            conn = get_db(); cur = conn.cursor()
            cur.execute("SELECT slug, atualizada_em FROM seo_paginas WHERE publicada = true ORDER BY id")
            for r in cur.fetchall():
                d = r["atualizada_em"].strftime("%Y-%m-%d") if r["atualizada_em"] else datetime.utcnow().strftime("%Y-%m-%d")
                out.append(f"""  <url>
    <loc>{base}/servicos/{r['slug']}</loc>
    <lastmod>{d}</lastmod>
    <changefreq>monthly</changefreq>
    <priority>0.8</priority>
  </url>""")
            cur.close(); conn.close()
        except Exception as e:
            print("[seo] sitemap:", e)
        return out

    return sitemap_urls
