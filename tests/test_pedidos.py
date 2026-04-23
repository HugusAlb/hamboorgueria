from fastapi.testclient import TestClient
from sqlmodel import Session
from app.models import Pedido, Produto, StatusPedido


def _criar_produto(session: Session) -> Produto:
    produto = Produto(nome="X-Burguer", preco=25.0)
    session.add(produto)
    session.commit()
    session.refresh(produto)
    return produto


def test_criar_pedido(client: TestClient):
    resp = client.post("/pedidos/", json={"mesa": 3})
    assert resp.status_code == 201
    data = resp.json()
    assert data["mesa"] == 3
    assert data["status"] == StatusPedido.aguardando


def test_listar_pedidos(client: TestClient, session: Session):
    session.add(Pedido(mesa=1))
    session.add(Pedido(mesa=2))
    session.commit()

    resp = client.get("/pedidos/")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_obter_pedido_inexistente(client: TestClient):
    resp = client.get("/pedidos/999")
    assert resp.status_code == 404


def test_atualizar_status(client: TestClient, session: Session):
    pedido = Pedido(mesa=5)
    session.add(pedido)
    session.commit()
    session.refresh(pedido)

    resp = client.patch(f"/pedidos/{pedido.id}/status?status=preparando")
    assert resp.status_code == 200
    assert resp.json()["status"] == StatusPedido.preparando


def test_adicionar_item(client: TestClient, session: Session):
    produto = _criar_produto(session)
    pedido = Pedido(mesa=2)
    session.add(pedido)
    session.commit()
    session.refresh(pedido)

    resp = client.post(
        f"/pedidos/{pedido.id}/itens",
        json={"produto_id": produto.id, "quantidade": 2},
    )
    assert resp.status_code == 201
    assert resp.json()["quantidade"] == 2


def test_adicionar_item_produto_inexistente(client: TestClient, session: Session):
    pedido = Pedido(mesa=1)
    session.add(pedido)
    session.commit()
    session.refresh(pedido)

    resp = client.post(
        f"/pedidos/{pedido.id}/itens",
        json={"produto_id": 9999, "quantidade": 1},
    )
    assert resp.status_code == 404
