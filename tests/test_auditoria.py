from fastapi.testclient import TestClient
from sqlmodel import Session
from app.models import ItemPedido, Movimento, Pagamento, Pedido, Produto, TipoMovimento


def test_registrar_movimento(client: TestClient):
    resp = client.post(
        "/api/auditoria/movimentos",
        json={"tipo": "saida", "descricao": "Fornecedor", "valor": 150.0},
    )
    assert resp.status_code == 201
    assert resp.json()["tipo"] == "saida"


def test_resumo_json_vazio(client: TestClient):
    resp = client.get("/api/auditoria/resumo")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"entradas": 0.0, "saidas": 0.0, "saldo": 0.0}


def test_resumo_json_com_movimentos(client: TestClient, session: Session):
    session.add(Movimento(tipo=TipoMovimento.entrada, descricao="Venda", valor=100.0))
    session.add(Movimento(tipo=TipoMovimento.saida, descricao="Gás", valor=40.0))
    session.commit()

    resp = client.get("/api/auditoria/resumo")
    data = resp.json()
    assert data["entradas"] == 100.0
    assert data["saidas"] == 40.0
    assert data["saldo"] == 60.0


def test_pagina_auditoria_mostra_saldo_caixa(auditoria_client: TestClient, session: Session):
    client = auditoria_client
    produto = Produto(nome="X-Burguer", preco=30.0)
    session.add(produto)
    pedido = Pedido(mesa=1)
    session.add(pedido)
    session.commit()
    session.refresh(produto)
    session.refresh(pedido)

    session.add(ItemPedido(pedido_id=pedido.id, produto_id=produto.id, quantidade=1))
    # Pagamento em dinheiro
    session.add(Pagamento(pedido_id=pedido.id, forma="dinheiro", valor_pago=30.0))
    # Saída manual
    session.add(Movimento(tipo=TipoMovimento.entrada, descricao="Venda #1", valor=30.0))
    session.add(Movimento(tipo=TipoMovimento.saida, descricao="Troco fornecedor", valor=10.0))
    session.commit()

    resp = client.get("/auditoria")
    assert resp.status_code == 200
    # Saldo caixa = 30 (dinheiro) - 10 (saida) = 20
    assert "20.00" in resp.text
    assert "Esperado no Caixa" in resp.text


def test_pagina_auditoria_breakdown_por_forma(auditoria_client: TestClient, session: Session):
    client = auditoria_client
    pedido1 = Pedido(mesa=1)
    pedido2 = Pedido(mesa=2)
    session.add(pedido1)
    session.add(pedido2)
    session.commit()
    session.refresh(pedido1)
    session.refresh(pedido2)

    session.add(Pagamento(pedido_id=pedido1.id, forma="pix", valor_pago=50.0))
    session.add(Pagamento(pedido_id=pedido2.id, forma="cartao_credito", valor_pago=80.0))
    session.commit()

    resp = client.get("/auditoria")
    assert "50.00" in resp.text  # pix
    assert "80.00" in resp.text  # crédito


def test_venda_aparece_no_historico(auditoria_client: TestClient, session: Session):
    client = auditoria_client
    produto = Produto(nome="Fritas", preco=12.0)
    session.add(produto)
    pedido = Pedido(mesa=3)
    session.add(pedido)
    session.commit()
    session.refresh(produto)
    session.refresh(pedido)
    session.add(ItemPedido(pedido_id=pedido.id, produto_id=produto.id, quantidade=1))
    session.commit()

    resp = client.post(
        f"/caixa/pedido/{pedido.id}/pagar",
        data={"forma": "pix", "valor_pago": "12.00"},
    )
    assert resp.status_code == 200

    resp = client.get("/auditoria")
    assert f"Pagamento pedido #{pedido.id}" in resp.text
