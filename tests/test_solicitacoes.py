import json
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from app.models import Cliente, ItemPedido, Pedido, Produto, SolicitacaoEdicao, StatusSolicitacao


def _setup(session: Session, client: TestClient):
    p1 = Produto(nome="X-Burguer", preco=25.0)
    p2 = Produto(nome="Fritas", preco=12.0)
    session.add(p1)
    session.add(p2)
    session.commit()
    session.refresh(p1)
    session.refresh(p2)

    client.post("/cliente/registrar", data={"nome": "Test", "telefone": "11900000001", "senha": "test123"})
    cliente = session.exec(select(Cliente).where(Cliente.telefone == "11900000001")).first()

    pedido = Pedido(mesa=1, cliente_id=cliente.id)
    session.add(pedido)
    session.commit()
    session.refresh(pedido)
    session.add(ItemPedido(pedido_id=pedido.id, produto_id=p1.id, quantidade=2))
    session.commit()

    return pedido, p1, p2


def test_solicitar_edicao(client: TestClient, session: Session):
    pedido, p1, p2 = _setup(session, client)
    resp = client.post(
        f"/cliente/pedido/{pedido.id}/solicitar-edicao",
        data={"mesa": "1", f"item_{p1.id}": "1", f"item_{p2.id}": "1"},
    )
    assert resp.status_code == 200
    assert "aprovação" in resp.text.lower() or "aguardando" in resp.text.lower()

    sol = session.exec(select(SolicitacaoEdicao).where(SolicitacaoEdicao.pedido_id == pedido.id)).first()
    assert sol is not None
    assert sol.status == StatusSolicitacao.pendente


def test_cozinha_ve_solicitacao(client: TestClient, session: Session):
    pedido, p1, p2 = _setup(session, client)
    client.post(f"/cliente/pedido/{pedido.id}/solicitar-edicao", data={f"item_{p2.id}": "2"})

    resp = client.get("/api/cozinha/solicitacoes")
    assert resp.status_code == 200
    assert "Fritas" in resp.text


def test_aprovar_edicao_substitui_itens(client: TestClient, session: Session):
    pedido, p1, p2 = _setup(session, client)
    client.post(f"/cliente/pedido/{pedido.id}/solicitar-edicao", data={f"item_{p2.id}": "3"})

    sol = session.exec(select(SolicitacaoEdicao).where(SolicitacaoEdicao.pedido_id == pedido.id)).first()
    resp = client.post(f"/cozinha/solicitacoes/{sol.id}/aprovar")
    assert resp.status_code == 200

    session.expire_all()
    sol = session.get(SolicitacaoEdicao, sol.id)
    assert sol.status == StatusSolicitacao.aprovado

    itens = session.exec(select(ItemPedido).where(ItemPedido.pedido_id == pedido.id)).all()
    assert len(itens) == 1
    assert itens[0].produto_id == p2.id
    assert itens[0].quantidade == 3


def test_rejeitar_edicao(client: TestClient, session: Session):
    pedido, p1, p2 = _setup(session, client)
    client.post(f"/cliente/pedido/{pedido.id}/solicitar-edicao", data={f"item_{p1.id}": "1"})

    sol = session.exec(select(SolicitacaoEdicao).where(SolicitacaoEdicao.pedido_id == pedido.id)).first()
    client.post(f"/cozinha/solicitacoes/{sol.id}/rejeitar")

    session.expire_all()
    sol = session.get(SolicitacaoEdicao, sol.id)
    assert sol.status == StatusSolicitacao.rejeitado
