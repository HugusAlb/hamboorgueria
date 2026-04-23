import asyncio
import json
import secrets
from collections import defaultdict
from contextlib import asynccontextmanager
from types import SimpleNamespace

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.database import create_db, get_session, seed_cliente_demo, seed_usuarios
from app.models import (
    Cliente, FormaPagamento, ItemPedido, Movimento,
    Pagamento, Pedido, Produto,
    SolicitacaoEdicao, StatusPedido, StatusSolicitacao, TipoEntrega, TipoMovimento,
    Usuario, gerar_codigo_pedido, hash_senha, verificar_senha,
)
from app.routers import auditoria, caixa, pedidos, produtos


# ── SSE broadcaster ──────────────────────────────────────────────────────────

class _Broadcaster:
    def __init__(self):
        self._queues: dict[str, list[asyncio.Queue]] = defaultdict(list)

    def subscribe(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=16)
        self._queues[channel].append(q)
        return q

    def unsubscribe(self, channel: str, q: asyncio.Queue):
        try:
            self._queues[channel].remove(q)
        except ValueError:
            pass

    async def broadcast(self, *channels: str):
        msg = "event: update\ndata: refresh\n\n"
        for ch in channels:
            for q in list(self._queues[ch]):
                try:
                    q.put_nowait(msg)
                except asyncio.QueueFull:
                    pass


_sse = _Broadcaster()


@asynccontextmanager
async def lifespan(app):
    create_db()
    seed_usuarios()
    yield


app = FastAPI(title="Hamboorgueria", lifespan=lifespan)
templates = Jinja2Templates(directory="app/templates")
templates.env.filters["fromjson"] = json.loads

app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(pedidos.router)
app.include_router(produtos.router, prefix="/api")
app.include_router(caixa.router, prefix="/api")
app.include_router(auditoria.router, prefix="/api")


@app.get("/sse/{channel}")
async def sse_stream(channel: str, request: Request):
    q = _sse.subscribe(channel)

    async def stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=25)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _sse.unsubscribe(channel, q)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_cliente(request: Request, session: Session) -> Cliente | None:
    token = request.cookies.get("cliente_token")
    if not token:
        return None
    return session.exec(select(Cliente).where(Cliente.token == token)).first()


def _pedido_ativo(cliente: Cliente, session: Session) -> Pedido | None:
    ativos = {StatusPedido.pendente, StatusPedido.aguardando, StatusPedido.preparando, StatusPedido.pronto}
    return session.exec(
        select(Pedido)
        .where(Pedido.cliente_id == cliente.id)
        .where(Pedido.status.in_(list(ativos)))
    ).first()


def _carregar_itens(lista: list[Pedido], session: Session):
    if not lista:
        return
    ids = [p.id for p in lista]
    itens = session.exec(select(ItemPedido).where(ItemPedido.pedido_id.in_(ids))).all()
    if itens:
        prod_ids = list({i.produto_id for i in itens})
        produtos = {p.id: p for p in session.exec(select(Produto).where(Produto.id.in_(prod_ids))).all()}
        for item in itens:
            item.produto = produtos.get(item.produto_id)
    por_pedido: dict[int, list] = {p.id: [] for p in lista}
    for item in itens:
        por_pedido[item.pedido_id].append(item)
    for pedido in lista:
        pedido.itens = por_pedido[pedido.id]


def _solicitacao_pendente(pedido_id: int, session: Session) -> SolicitacaoEdicao | None:
    return session.exec(
        select(SolicitacaoEdicao)
        .where(SolicitacaoEdicao.pedido_id == pedido_id)
        .where(SolicitacaoEdicao.status == StatusSolicitacao.pendente)
    ).first()


def _calcular_resumo(session: Session) -> dict:
    from sqlmodel import func

    mov_rows = session.exec(
        select(Movimento.tipo, func.sum(Movimento.valor)).group_by(Movimento.tipo)
    ).all()
    mov = {tipo: round(val or 0.0, 2) for tipo, val in mov_rows}

    pag_rows = session.exec(
        select(Pagamento.forma, func.sum(Pagamento.valor_pago)).group_by(Pagamento.forma)
    ).all()
    pag = {forma: round(val or 0.0, 2) for forma, val in pag_rows}

    entradas = mov.get(TipoMovimento.entrada, 0.0)
    saidas   = mov.get(TipoMovimento.saida,   0.0)
    por_forma = {f.value: pag.get(f, 0.0) for f in FormaPagamento}
    dinheiro_pix = por_forma[FormaPagamento.dinheiro.value] + por_forma[FormaPagamento.pix.value]

    return {
        "entradas": entradas,
        "saidas": saidas,
        "saldo": round(entradas - saidas, 2),
        "por_forma": por_forma,
        "saldo_caixa": round(dinheiro_pix - saidas, 2),
    }


ACESSO: dict[str, set[str]] = {
    "/cozinha":   {"cozinha", "auditoria"},
    "/caixa":     {"caixa",   "auditoria"},
    "/auditoria": {"auditoria"},
}


def _get_usuario(request: Request, session: Session) -> Usuario | None:
    token = request.cookies.get("usuario_token")
    if not token:
        return None
    return session.exec(select(Usuario).where(Usuario.token == token)).first()


def _checar_acesso(
    request: Request, session: Session, rota: str
) -> tuple[Usuario | None, RedirectResponse | None]:
    usuario = _get_usuario(request, session)
    if not usuario:
        return None, RedirectResponse(url=f"/login?next={rota}", status_code=303)
    if usuario.perfil not in ACESSO[rota]:
        return None, RedirectResponse(url=f"/{usuario.perfil}", status_code=303)
    return usuario, None


def _tabela_produtos(request: Request, session: Session) -> HTMLResponse:
    prods = session.exec(select(Produto)).all()
    return templates.TemplateResponse(request, "partials/tabela_produtos.html", {"produtos": prods})



# ── Páginas ───────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "base.html")


@app.get("/login", response_class=HTMLResponse)
def pagina_login(request: Request, next: str = "/"):
    return templates.TemplateResponse(request, "login.html", {"next": next})


@app.post("/login", response_class=HTMLResponse)
async def processar_login(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    login = form["login"].strip()
    senha = form["senha"]
    next_url = form.get("next", "/")

    usuario = session.exec(select(Usuario).where(Usuario.login == login)).first()
    if not usuario or not verificar_senha(senha, usuario.senha_hash):
        return templates.TemplateResponse(request, "login.html", {
            "next": next_url,
            "erro": "Login ou senha incorretos.",
        })

    usuario.token = secrets.token_urlsafe(32)
    session.add(usuario)
    session.commit()

    perfis_next = ACESSO.get(next_url, set())
    destino = next_url if usuario.perfil in perfis_next else f"/{usuario.perfil}"

    resp = RedirectResponse(url=destino, status_code=303)
    resp.set_cookie("usuario_token", usuario.token, httponly=True, max_age=86400, samesite="lax")
    return resp


@app.get("/logout")
def logout(request: Request, session: Session = Depends(get_session)):
    token = request.cookies.get("usuario_token")
    if token:
        usuario = session.exec(select(Usuario).where(Usuario.token == token)).first()
        if usuario:
            usuario.token = None
            session.add(usuario)
            session.commit()
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("usuario_token")
    return resp


@app.get("/cozinha", response_class=HTMLResponse)
def pagina_cozinha(request: Request, session: Session = Depends(get_session)):
    usuario, redir = _checar_acesso(request, session, "/cozinha")
    if redir:
        return redir
    solicitacoes = session.exec(
        select(SolicitacaoEdicao).where(SolicitacaoEdicao.status == StatusSolicitacao.pendente)
    ).all()
    pedidos_ativos = session.exec(
        select(Pedido).where(Pedido.status.in_([StatusPedido.aguardando, StatusPedido.preparando]))
    ).all()
    _carregar_itens(pedidos_ativos, session)
    return templates.TemplateResponse(request, "cozinha.html", {
        "solicitacoes": solicitacoes,
        "pedidos": pedidos_ativos,
        "usuario": usuario,
    })


@app.get("/cliente", response_class=HTMLResponse)
def pagina_cliente(request: Request, session: Session = Depends(get_session)):
    cliente = _get_cliente(request, session)
    if not cliente:
        return templates.TemplateResponse(request, "cliente.html", {"cliente": None})

    ativo = _pedido_ativo(cliente, session)
    if ativo:
        _carregar_itens([ativo], session)
        sol = _solicitacao_pendente(ativo.id, session)
        return templates.TemplateResponse(request, "cliente.html", {
            "cliente": cliente,
            "pedido_ativo": ativo,
            "solicitacao": sol,
        })

    prods = session.exec(select(Produto).where(Produto.disponivel == True)).all()
    return templates.TemplateResponse(request, "cliente.html", {
        "cliente": cliente,
        "pedido_ativo": None,
        "produtos": prods,
        "historico": _historico_cliente(cliente, session),
    })


@app.get("/caixa", response_class=HTMLResponse)
def pagina_caixa(request: Request, session: Session = Depends(get_session)):
    usuario, redir = _checar_acesso(request, session, "/caixa")
    if redir:
        return redir
    prods = session.exec(select(Produto).where(Produto.disponivel == True)).all()
    pendentes = session.exec(select(Pedido).where(Pedido.status == StatusPedido.pendente)).all()
    _carregar_itens(pendentes, session)
    aceitos_status = [StatusPedido.aguardando, StatusPedido.preparando, StatusPedido.pronto]
    aceitos = session.exec(select(Pedido).where(Pedido.status.in_(aceitos_status))).all()
    _carregar_itens(aceitos, session)
    return templates.TemplateResponse(request, "caixa.html", {
        "usuario": usuario,
        "produtos": prods,
        "pedidos_pendentes": pendentes,
        "pedidos_aceitos": aceitos,
    })


@app.get("/auditoria", response_class=HTMLResponse)
def pagina_auditoria(request: Request, session: Session = Depends(get_session)):
    usuario, redir = _checar_acesso(request, session, "/auditoria")
    if redir:
        return redir
    movimentos = session.exec(select(Movimento).order_by(Movimento.registrado_em.desc())).all()
    prods = session.exec(select(Produto)).all()
    return templates.TemplateResponse(
        request, "auditoria.html",
        {"movimentos": movimentos, "resumo": _calcular_resumo(session), "produtos": prods, "usuario": usuario},
    )


@app.get("/api/auditoria/resumo-html", response_class=HTMLResponse)
def fragmento_auditoria_resumo(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request, "partials/auditoria_resumo.html", {"resumo": _calcular_resumo(session)}
    )


# ── Cliente: registro / login / logout ────────────────────────────────────────

def _historico_cliente(cliente: Cliente, session: Session) -> list[Pedido]:
    concluidos = [StatusPedido.entregue, StatusPedido.cancelado]
    pedidos = session.exec(
        select(Pedido)
        .where(Pedido.cliente_id == cliente.id)
        .where(Pedido.status.in_(concluidos))
        .order_by(Pedido.criado_em.desc())
        .limit(5)
    ).all()
    _carregar_itens(pedidos, session)
    return pedidos


def _resposta_cliente_logado(request: Request, cliente: Cliente, session: Session):
    ativo = _pedido_ativo(cliente, session)
    if ativo:
        _carregar_itens([ativo], session)
        sol = _solicitacao_pendente(ativo.id, session)
        resp = templates.TemplateResponse(request, "cliente.html", {
            "cliente": cliente, "pedido_ativo": ativo, "solicitacao": sol,
        })
    else:
        prods = session.exec(select(Produto).where(Produto.disponivel == True)).all()
        resp = templates.TemplateResponse(request, "cliente.html", {
            "cliente": cliente, "pedido_ativo": None,
            "produtos": prods, "historico": _historico_cliente(cliente, session),
        })
    resp.set_cookie("cliente_token", cliente.token, httponly=True, max_age=86400 * 30, samesite="lax")
    return resp


@app.post("/cliente/registrar", response_class=HTMLResponse)
async def cliente_registrar(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    nome = form["nome"].strip()
    telefone = form["telefone"].strip()
    senha = form.get("senha", "").strip()

    if not senha:
        return templates.TemplateResponse(request, "cliente.html", {
            "cliente": None,
            "erro_cadastrar": "A senha é obrigatória.",
        })

    if session.exec(select(Cliente).where(Cliente.telefone == telefone)).first():
        return templates.TemplateResponse(request, "cliente.html", {
            "cliente": None,
            "erro_cadastrar": "Este telefone já está cadastrado. Use a aba Entrar.",
        })

    cliente = Cliente(
        nome=nome,
        telefone=telefone,
        senha_hash=hash_senha(senha),
    )
    session.add(cliente)
    session.commit()
    session.refresh(cliente)
    return _resposta_cliente_logado(request, cliente, session)


@app.post("/cliente/entrar", response_class=HTMLResponse)
async def cliente_entrar(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    telefone = form["telefone"].strip()
    senha = form.get("senha", "").strip()

    cliente = session.exec(select(Cliente).where(Cliente.telefone == telefone)).first()
    if not cliente:
        return templates.TemplateResponse(request, "cliente.html", {
            "cliente": None,
            "erro_entrar": "Telefone não encontrado. Faça seu cadastro.",
        })

    if not senha or not cliente.senha_hash or not verificar_senha(senha, cliente.senha_hash):
        return templates.TemplateResponse(request, "cliente.html", {
            "cliente": None,
            "erro_entrar": "Telefone ou senha incorretos.",
        })

    return _resposta_cliente_logado(request, cliente, session)


@app.get("/cliente/sair")
def cliente_sair():
    resp = RedirectResponse(url="/cliente", status_code=303)
    resp.delete_cookie("cliente_token")
    return resp


# ── Cliente: pedido ───────────────────────────────────────────────────────────

@app.post("/cliente/pedido", response_class=HTMLResponse)
async def cliente_criar_pedido(request: Request, session: Session = Depends(get_session)):
    cliente = _get_cliente(request, session)
    if not cliente:
        return HTMLResponse("<p class='text-red-500 font-semibold'>Faça login para realizar um pedido.</p>")

    if _pedido_ativo(cliente, session):
        return HTMLResponse("<p class='text-yellow-600 font-semibold'>Você já tem um pedido em andamento.</p>")

    form = await request.form()
    pedido = Pedido(
        tipo_entrega=TipoEntrega(form.get("tipo_entrega", "local")),
        observacao=form.get("observacao") or None,
        cliente_id=cliente.id,
        status=StatusPedido.pendente,
    )
    session.add(pedido)
    session.commit()
    session.refresh(pedido)

    itens_confirmados = []
    for chave, valor in form.items():
        if not chave.startswith("item_"):
            continue
        quantidade = int(valor)
        if quantidade <= 0:
            continue
        produto_id = int(chave.removeprefix("item_"))
        produto = session.get(Produto, produto_id)
        if not produto:
            continue
        session.add(ItemPedido(pedido_id=pedido.id, produto_id=produto_id, quantidade=quantidade))
        itens_confirmados.append({"quantidade": quantidade, "nome": produto.nome})

    session.commit()
    await _sse.broadcast("caixa")
    resp = Response(status_code=200)
    resp.headers["HX-Redirect"] = "/cliente"
    return resp


# ── Cliente: solicitar edição ─────────────────────────────────────────────────

@app.get("/cliente/pedido/{pedido_id}/editar", response_class=HTMLResponse)
def cliente_form_edicao(pedido_id: int, request: Request, session: Session = Depends(get_session)):
    cliente = _get_cliente(request, session)
    pedido = session.get(Pedido, pedido_id)
    if not pedido or not cliente or pedido.cliente_id != cliente.id:
        return HTMLResponse("<p class='text-red-500'>Não autorizado.</p>")
    if pedido.status == StatusPedido.pronto:
        return HTMLResponse("<p class='text-yellow-600'>Pedido já está pronto, não pode ser editado.</p>")
    if _solicitacao_pendente(pedido_id, session):
        return HTMLResponse("<p class='text-yellow-600'>Já existe uma edição aguardando aprovação.</p>")

    _carregar_itens([pedido], session)
    prods = session.exec(select(Produto).where(Produto.disponivel == True)).all()
    return templates.TemplateResponse(request, "partials/form_edicao_pedido.html", {
        "pedido": pedido,
        "produtos": prods,
    })


@app.post("/cliente/pedido/{pedido_id}/solicitar-edicao", response_class=HTMLResponse)
async def cliente_solicitar_edicao(pedido_id: int, request: Request, session: Session = Depends(get_session)):
    cliente = _get_cliente(request, session)
    pedido = session.get(Pedido, pedido_id)
    if not pedido or not cliente or pedido.cliente_id != cliente.id:
        return HTMLResponse("<p class='text-red-500'>Não autorizado.</p>")

    _carregar_itens([pedido], session)
    itens_antes = [
        {"produto_id": i.produto_id, "nome": i.produto.nome, "quantidade": i.quantidade}
        for i in pedido.itens
    ]

    form = await request.form()
    itens_depois = []
    for chave, valor in form.items():
        if not chave.startswith("item_"):
            continue
        quantidade = int(valor)
        if quantidade <= 0:
            continue
        produto_id = int(chave.removeprefix("item_"))
        produto = session.get(Produto, produto_id)
        if produto:
            itens_depois.append({"produto_id": produto_id, "nome": produto.nome, "quantidade": quantidade})

    sol = SolicitacaoEdicao(
        pedido_id=pedido_id,
        itens_antes=json.dumps(itens_antes, ensure_ascii=False),
        itens_depois=json.dumps(itens_depois, ensure_ascii=False),
        obs_antes=pedido.observacao,
        obs_depois=form.get("observacao") or None,
    )
    session.add(sol)
    session.commit()
    session.refresh(sol)
    await _sse.broadcast("cozinha")

    return templates.TemplateResponse(request, "partials/edicao_aguardando.html", {
        "pedido": pedido,
        "solicitacao": sol,
    })


# ── Cozinha: solicitações de edição ──────────────────────────────────────────

@app.get("/api/cozinha/solicitacoes", response_class=HTMLResponse)
def fragmento_solicitacoes(request: Request, session: Session = Depends(get_session)):
    solicitacoes = session.exec(
        select(SolicitacaoEdicao).where(SolicitacaoEdicao.status == StatusSolicitacao.pendente)
    ).all()
    return templates.TemplateResponse(request, "partials/solicitacoes_edicao.html", {"solicitacoes": solicitacoes})


@app.post("/cozinha/solicitacoes/{sol_id}/aprovar", response_class=HTMLResponse)
async def cozinha_aprovar_edicao(sol_id: int, request: Request, session: Session = Depends(get_session)):
    sol = session.get(SolicitacaoEdicao, sol_id)
    if not sol or sol.status != StatusSolicitacao.pendente:
        return HTMLResponse("")

    itens_antigos = session.exec(select(ItemPedido).where(ItemPedido.pedido_id == sol.pedido_id)).all()
    for item in itens_antigos:
        session.delete(item)

    novos = json.loads(sol.itens_depois)
    for item in novos:
        session.add(ItemPedido(
            pedido_id=sol.pedido_id,
            produto_id=item["produto_id"],
            quantidade=item["quantidade"],
        ))

    pedido = session.get(Pedido, sol.pedido_id)
    if sol.obs_depois is not None:
        pedido.observacao = sol.obs_depois
        session.add(pedido)

    sol.status = StatusSolicitacao.aprovado
    session.add(sol)
    session.commit()
    await _sse.broadcast("cozinha", "pedido")

    return _fragmento_solicitacoes_atualizado(request, session)


@app.post("/cozinha/solicitacoes/{sol_id}/rejeitar", response_class=HTMLResponse)
async def cozinha_rejeitar_edicao(sol_id: int, request: Request, session: Session = Depends(get_session)):
    sol = session.get(SolicitacaoEdicao, sol_id)
    if sol:
        sol.status = StatusSolicitacao.rejeitado
        session.add(sol)
        session.commit()
    await _sse.broadcast("cozinha", "pedido")
    return _fragmento_solicitacoes_atualizado(request, session)


@app.post("/cozinha/pedido/{pedido_id}/status", response_class=HTMLResponse)
async def cozinha_atualizar_status(
    pedido_id: int, status: StatusPedido, request: Request, session: Session = Depends(get_session)
):
    pedido = session.get(Pedido, pedido_id)
    if pedido:
        pedido.status = status
        session.add(pedido)
        session.commit()
    await _sse.broadcast("cozinha", "pedido", "caixa")
    return fragmento_pedidos_ativos(request, session)


def _fragmento_solicitacoes_atualizado(request: Request, session: Session) -> HTMLResponse:
    solicitacoes = session.exec(
        select(SolicitacaoEdicao).where(SolicitacaoEdicao.status == StatusSolicitacao.pendente)
    ).all()
    return templates.TemplateResponse(request, "partials/solicitacoes_edicao.html", {"solicitacoes": solicitacoes})


# ── Fragmentos HTMX ───────────────────────────────────────────────────────────

@app.get("/api/cozinha/pedidos-ativos", response_class=HTMLResponse)
def fragmento_pedidos_ativos(request: Request, session: Session = Depends(get_session)):
    pedidos_ativos = session.exec(
        select(Pedido).where(Pedido.status.in_([StatusPedido.aguardando, StatusPedido.preparando]))
    ).all()
    _carregar_itens(pedidos_ativos, session)
    return templates.TemplateResponse(request, "partials/pedidos_cozinha.html", {"pedidos": pedidos_ativos})


@app.get("/api/cliente/pedido", response_class=HTMLResponse)
def fragmento_status_pedido(pedido_id: int, request: Request, session: Session = Depends(get_session)):
    pedido = session.get(Pedido, pedido_id)
    if not pedido:
        return HTMLResponse("<p class='text-red-500'>Pedido não encontrado.</p>")
    _carregar_itens([pedido], session)
    sol_rejeitada = session.exec(
        select(SolicitacaoEdicao)
        .where(SolicitacaoEdicao.pedido_id == pedido_id)
        .where(SolicitacaoEdicao.status == StatusSolicitacao.rejeitado)
    ).first()
    return templates.TemplateResponse(request, "partials/status_pedido.html", {
        "pedido": pedido,
        "sol_rejeitada": sol_rejeitada,
    })


@app.get("/api/caixa/pedido", response_class=HTMLResponse)
def fragmento_caixa_pedido(codigo: str, request: Request, session: Session = Depends(get_session)):
    pedido = session.exec(select(Pedido).where(Pedido.codigo == codigo.upper())).first()
    if not pedido:
        return HTMLResponse("<p class='text-red-500'>Pedido não encontrado.</p>")
    _carregar_itens([pedido], session)
    total = sum(
        item.quantidade * session.get(Produto, item.produto_id).preco
        for item in pedido.itens
    )
    return templates.TemplateResponse(request, "partials/caixa_pedido.html", {"pedido": pedido, "total": total})


@app.post("/caixa/pedido/{pedido_id}/pagar", response_class=HTMLResponse)
async def fragmento_confirmar_pagamento(
    pedido_id: int, request: Request, session: Session = Depends(get_session),
):
    form = await request.form()
    pedido = session.get(Pedido, pedido_id)
    if not pedido:
        return HTMLResponse("<p class='text-red-500'>Pedido não encontrado.</p>")
    if pedido.pagamento:
        return HTMLResponse("<p class='text-yellow-600'>Pedido já foi pago.</p>")

    session.add(Pagamento(pedido_id=pedido_id, forma=form["forma"], valor_pago=float(form["valor_pago"])))
    pedido.status = StatusPedido.entregue
    session.add(pedido)
    session.add(Movimento(
        tipo=TipoMovimento.entrada,
        descricao=f"Pagamento pedido #{pedido_id} ({form['forma']})",
        valor=float(form["valor_pago"]),
    ))
    session.commit()
    await _sse.broadcast("caixa", "auditoria", "pedido")
    session.refresh(pedido)
    _carregar_itens([pedido], session)
    total = sum(item.quantidade * session.get(Produto, item.produto_id).preco for item in pedido.itens)

    pedido_html = templates.get_template("partials/caixa_pedido.html").render(
        {"pedido": pedido, "total": total, "request": request}
    )
    aceitos_status = [StatusPedido.aguardando, StatusPedido.preparando, StatusPedido.pronto]
    aceitos = session.exec(select(Pedido).where(Pedido.status.in_(aceitos_status))).all()
    _carregar_itens(aceitos, session)
    aceitos_html = templates.get_template("partials/caixa_aceitos.html").render(
        {"pedidos": aceitos, "request": request}
    )
    oob = f'<div id="aceitos-lista" hx-swap-oob="innerHTML">{aceitos_html}</div>'
    return HTMLResponse(pedido_html + oob)


# ── Caixa: aprovar / rejeitar pedido ─────────────────────────────────────────

def _fragmento_pendentes(request: Request, session: Session) -> HTMLResponse:
    pedidos = session.exec(select(Pedido).where(Pedido.status == StatusPedido.pendente)).all()
    _carregar_itens(pedidos, session)
    return templates.TemplateResponse(request, "partials/caixa_pendentes.html", {"pedidos": pedidos})


def _fragmento_aceitos(request: Request, session: Session) -> HTMLResponse:
    aceitos = [StatusPedido.aguardando, StatusPedido.preparando, StatusPedido.pronto]
    pedidos = session.exec(select(Pedido).where(Pedido.status.in_(aceitos))).all()
    _carregar_itens(pedidos, session)
    return templates.TemplateResponse(request, "partials/caixa_aceitos.html", {"pedidos": pedidos})


@app.get("/api/caixa/pendentes", response_class=HTMLResponse)
def fragmento_caixa_pendentes(request: Request, session: Session = Depends(get_session)):
    return _fragmento_pendentes(request, session)


@app.get("/api/caixa/aceitos", response_class=HTMLResponse)
def fragmento_caixa_aceitos(request: Request, session: Session = Depends(get_session)):
    return _fragmento_aceitos(request, session)


@app.post("/caixa/pedido/{pedido_id}/aprovar", response_class=HTMLResponse)
async def caixa_aprovar_pedido(pedido_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    pedido = session.get(Pedido, pedido_id)
    if not pedido or pedido.status != StatusPedido.pendente:
        return _fragmento_pendentes(request, session)
    if pedido.tipo_entrega == TipoEntrega.local:
        mesa = form.get("mesa")
        if not mesa:
            return _fragmento_pendentes(request, session)
        pedido.mesa = int(mesa)
    pedido.status = StatusPedido.aguardando
    session.add(pedido)
    session.commit()
    await _sse.broadcast("caixa", "cozinha", "pedido")
    return _fragmento_pendentes(request, session)


@app.post("/caixa/pedido/{pedido_id}/rejeitar", response_class=HTMLResponse)
async def caixa_rejeitar_pedido(pedido_id: int, request: Request, session: Session = Depends(get_session)):
    pedido = session.get(Pedido, pedido_id)
    if pedido and pedido.status == StatusPedido.pendente:
        pedido.status = StatusPedido.cancelado
        session.add(pedido)
        session.commit()
    await _sse.broadcast("caixa", "pedido")
    return _fragmento_pendentes(request, session)


@app.post("/caixa/registrar-pedido", response_class=HTMLResponse)
async def caixa_registrar_pedido(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    tipo = TipoEntrega(form.get("tipo_entrega", "local"))
    mesa = int(form["mesa"]) if tipo == TipoEntrega.local and form.get("mesa") else None

    pedido = Pedido(
        tipo_entrega=tipo,
        mesa=mesa,
        observacao=form.get("observacao") or None,
        status=StatusPedido.aguardando,
    )
    session.add(pedido)
    session.commit()
    session.refresh(pedido)

    adicionou = False
    for chave, valor in form.items():
        if not chave.startswith("item_"):
            continue
        quantidade = int(valor)
        if quantidade <= 0:
            continue
        produto_id = int(chave.removeprefix("item_"))
        if session.get(Produto, produto_id):
            session.add(ItemPedido(pedido_id=pedido.id, produto_id=produto_id, quantidade=quantidade))
            adicionou = True

    if not adicionou:
        session.delete(pedido)
        session.commit()
        return HTMLResponse("<p class='text-red-500 font-semibold'>Selecione ao menos um item.</p>")

    session.commit()
    return HTMLResponse(
        f"<div class='bg-green-50 border border-green-300 rounded-xl p-4 text-green-700 font-semibold'>"
        f"Pedido <strong>#{pedido.id}</strong> registrado e enviado para a cozinha!"
        f"</div>"
    )


# ── Demo ─────────────────────────────────────────────────────────────────────

_DEMO_USUARIOS = {
    "caixa":     SimpleNamespace(login="caixa (demo)",     perfil="caixa"),
    "cozinha":   SimpleNamespace(login="cozinha (demo)",   perfil="cozinha"),
    "auditoria": SimpleNamespace(login="auditoria (demo)", perfil="auditoria"),
}


@app.get("/demo", response_class=HTMLResponse)
def pagina_demo(request: Request):
    return templates.TemplateResponse(request, "demo.html", {})


@app.get("/demo/caixa", response_class=HTMLResponse)
def demo_caixa(request: Request, session: Session = Depends(get_session)):
    usuario = _DEMO_USUARIOS["caixa"]
    prods = session.exec(select(Produto).where(Produto.disponivel == True)).all()
    pendentes = session.exec(select(Pedido).where(Pedido.status == StatusPedido.pendente)).all()
    _carregar_itens(pendentes, session)
    aceitos_status = [StatusPedido.aguardando, StatusPedido.preparando, StatusPedido.pronto]
    aceitos = session.exec(select(Pedido).where(Pedido.status.in_(aceitos_status))).all()
    _carregar_itens(aceitos, session)
    return templates.TemplateResponse(request, "caixa.html", {
        "usuario": usuario, "produtos": prods,
        "pedidos_pendentes": pendentes, "pedidos_aceitos": aceitos,
    })


@app.get("/demo/cozinha", response_class=HTMLResponse)
def demo_cozinha(request: Request, session: Session = Depends(get_session)):
    usuario = _DEMO_USUARIOS["cozinha"]
    solicitacoes = session.exec(
        select(SolicitacaoEdicao).where(SolicitacaoEdicao.status == StatusSolicitacao.pendente)
    ).all()
    pedidos_ativos = session.exec(
        select(Pedido).where(Pedido.status.in_([StatusPedido.aguardando, StatusPedido.preparando]))
    ).all()
    _carregar_itens(pedidos_ativos, session)
    return templates.TemplateResponse(request, "cozinha.html", {
        "usuario": usuario, "solicitacoes": solicitacoes, "pedidos": pedidos_ativos,
    })


@app.get("/demo/auditoria", response_class=HTMLResponse)
def demo_auditoria(request: Request, session: Session = Depends(get_session)):
    usuario = _DEMO_USUARIOS["auditoria"]
    movimentos = session.exec(select(Movimento).order_by(Movimento.registrado_em.desc())).all()
    prods = session.exec(select(Produto)).all()
    return templates.TemplateResponse(request, "auditoria.html", {
        "movimentos": movimentos, "resumo": _calcular_resumo(session),
        "produtos": prods, "usuario": usuario,
    })


@app.get("/demo/cliente", response_class=HTMLResponse)
def demo_cliente(request: Request, session: Session = Depends(get_session)):
    cliente = session.exec(select(Cliente).where(Cliente.telefone == "00000000000")).first()
    if not cliente:
        prods = session.exec(select(Produto).where(Produto.disponivel == True)).all()
        return templates.TemplateResponse(request, "cliente.html", {"cliente": None, "produtos": prods})
    ativo = _pedido_ativo(cliente, session)
    if ativo:
        _carregar_itens([ativo], session)
        sol = _solicitacao_pendente(ativo.id, session)
        return templates.TemplateResponse(request, "cliente.html", {
            "cliente": cliente, "pedido_ativo": ativo, "solicitacao": sol,
        })
    prods = session.exec(select(Produto).where(Produto.disponivel == True)).all()
    return templates.TemplateResponse(request, "cliente.html", {
        "cliente": cliente,
        "pedido_ativo": None,
        "produtos": prods,
        "historico": _historico_cliente(cliente, session),
    })


# ── Admin: produtos ───────────────────────────────────────────────────────────

@app.post("/admin/produtos", response_class=HTMLResponse)
async def admin_criar_produto(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    session.add(Produto(nome=form["nome"], preco=float(form["preco"])))
    session.commit()
    return _tabela_produtos(request, session)


@app.get("/admin/produtos/{produto_id}/editar", response_class=HTMLResponse)
def admin_editar_produto(produto_id: int, request: Request, session: Session = Depends(get_session)):
    produto = session.get(Produto, produto_id)
    if not produto:
        return HTMLResponse("<tr><td colspan='4' class='text-red-500 px-4 py-3'>Produto não encontrado.</td></tr>")
    return templates.TemplateResponse(request, "partials/produto_editar.html", {"produto": produto})


@app.get("/admin/produtos/{produto_id}/cancelar", response_class=HTMLResponse)
def admin_cancelar_edicao(produto_id: int, request: Request, session: Session = Depends(get_session)):
    produto = session.get(Produto, produto_id)
    if not produto:
        return HTMLResponse("")
    return templates.TemplateResponse(request, "partials/produto_linha.html", {"produto": produto})


@app.patch("/admin/produtos/{produto_id}", response_class=HTMLResponse)
async def admin_salvar_produto(produto_id: int, request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    produto = session.get(Produto, produto_id)
    if not produto:
        return HTMLResponse("<tr><td colspan='4' class='text-red-500 px-4 py-3'>Produto não encontrado.</td></tr>")
    produto.nome = form["nome"]
    produto.preco = float(form["preco"])
    produto.disponivel = form["disponivel"] == "true"
    session.add(produto)
    session.commit()
    session.refresh(produto)
    return templates.TemplateResponse(request, "partials/produto_linha.html", {"produto": produto})


@app.patch("/admin/produtos/{produto_id}/disponibilidade", response_class=HTMLResponse)
def admin_toggle_disponibilidade(produto_id: int, request: Request, session: Session = Depends(get_session)):
    produto = session.get(Produto, produto_id)
    if not produto:
        return HTMLResponse("")
    produto.disponivel = not produto.disponivel
    session.add(produto)
    session.commit()
    session.refresh(produto)
    return templates.TemplateResponse(request, "partials/produto_linha.html", {"produto": produto})


@app.delete("/admin/produtos/{produto_id}", response_class=HTMLResponse)
def admin_apagar_produto(produto_id: int, request: Request, session: Session = Depends(get_session)):
    produto = session.get(Produto, produto_id)
    if produto:
        session.delete(produto)
        session.commit()
    return _tabela_produtos(request, session)


# ── Auditoria ─────────────────────────────────────────────────────────────────

@app.post("/auditoria/movimentos", response_class=HTMLResponse)
async def fragmento_registrar_movimento(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    session.add(Movimento(tipo=TipoMovimento(form["tipo"]), descricao=form["descricao"], valor=float(form["valor"])))
    session.commit()
    movimentos = session.exec(select(Movimento).order_by(Movimento.registrado_em.desc())).all()
    lista_html = templates.get_template("partials/movimentos_lista.html").render(
        {"movimentos": movimentos, "request": request}
    )
    resumo_html = templates.get_template("partials/auditoria_resumo.html").render(
        {"resumo": _calcular_resumo(session), "request": request}
    )
    oob = f'<div id="auditoria-resumo" hx-swap-oob="outerHTML">{resumo_html}</div>'
    return HTMLResponse(lista_html + oob)
