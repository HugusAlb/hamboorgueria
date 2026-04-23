from fastapi.testclient import TestClient
from sqlmodel import Session
from app.models import ItemPedido, Pedido, Produto


def _setup(session: Session):
    produto = Produto(nome="X-Bacon", preco=30.0)
    session.add(produto)
    pedido = Pedido(mesa=1)
    session.add(pedido)
    session.commit()
    session.refresh(produto)
    session.refresh(pedido)
    item = ItemPedido(pedido_id=pedido.id, produto_id=produto.id, quantidade=2)
    session.add(item)
    session.commit()
    return pedido, produto


def test_calcular_total(client: TestClient, session: Session):
    pedido, _ = _setup(session)
    resp = client.get(f"/api/caixa/pedido/{pedido.id}/total")
    assert resp.status_code == 200
    assert resp.json()["total"] == 60.0


def test_registrar_pagamento(client: TestClient, session: Session):
    pedido, _ = _setup(session)
    resp = client.post(
        f"/api/caixa/pedido/{pedido.id}/pagar",
        json={"forma": "pix", "valor_pago": 60.0},
    )
    assert resp.status_code == 201
    assert resp.json()["forma"] == "pix"


def test_pagamento_duplicado(client: TestClient, session: Session):
    pedido, _ = _setup(session)
    client.post(f"/api/caixa/pedido/{pedido.id}/pagar", json={"forma": "pix", "valor_pago": 60.0})
    resp = client.post(f"/api/caixa/pedido/{pedido.id}/pagar", json={"forma": "dinheiro", "valor_pago": 60.0})
    assert resp.status_code == 409
