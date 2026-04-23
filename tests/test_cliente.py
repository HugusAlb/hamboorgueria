from fastapi.testclient import TestClient
from sqlmodel import Session
from app.models import Cliente, Produto


def _registrar(client: TestClient, nome="Ana", telefone="11999990001", senha="test123"):
    client.post("/cliente/registrar", data={"nome": nome, "telefone": telefone, "senha": senha})


def test_registro_cria_cliente(client: TestClient, session: Session):
    resp = client.post("/cliente/registrar", data={"nome": "João", "telefone": "11999990002", "senha": "test123"})
    assert resp.status_code == 200
    assert session.exec(__import__("sqlmodel").select(Cliente).where(Cliente.telefone == "11999990002")).first()


def test_login_telefone_existente_nao_duplica(client: TestClient, session: Session):
    client.post("/cliente/registrar", data={"nome": "João", "telefone": "11999990003", "senha": "test123"})
    client.post("/cliente/registrar", data={"nome": "João2", "telefone": "11999990003", "senha": "test123"})
    from sqlmodel import select
    clientes = session.exec(select(Cliente).where(Cliente.telefone == "11999990003")).all()
    assert len(clientes) == 1


def test_fazer_pedido_requer_login(client: TestClient, session: Session):
    produto = Produto(nome="X-Burguer", preco=25.0)
    session.add(produto)
    session.commit()
    session.refresh(produto)
    # Sem cookie de sessão
    resp = client.post("/cliente/pedido", data={"mesa": "3", f"item_{produto.id}": "1"})
    assert resp.status_code == 200
    assert "login" in resp.text.lower() or "faça" in resp.text.lower()


def test_fazer_pedido_logado(client: TestClient, session: Session):
    produto = Produto(nome="X-Burguer", preco=25.0)
    session.add(produto)
    session.commit()
    session.refresh(produto)

    _registrar(client)
    resp = client.post("/cliente/pedido", data={"mesa": "3", f"item_{produto.id}": "2"})
    assert resp.status_code == 200
    assert resp.headers.get("HX-Redirect") == "/cliente"


def test_um_pedido_ativo_por_cliente(client: TestClient, session: Session):
    produto = Produto(nome="X-Burguer", preco=25.0)
    session.add(produto)
    session.commit()
    session.refresh(produto)

    _registrar(client, telefone="11999990010")
    client.post("/cliente/pedido", data={"mesa": "1", f"item_{produto.id}": "1"})
    resp = client.post("/cliente/pedido", data={"mesa": "2", f"item_{produto.id}": "1"})
    assert "andamento" in resp.text.lower() or "ativo" in resp.text.lower()


def test_pagina_cliente_sem_login_mostra_registro(client: TestClient):
    resp = client.get("/cliente")
    assert resp.status_code == 200
    assert "telefone" in resp.text.lower()


def test_pagina_cliente_logado_mostra_menu(client: TestClient, session: Session):
    session.add(Produto(nome="Smash Burguer", preco=32.0))
    session.commit()
    _registrar(client, telefone="11999990020")
    resp = client.get("/cliente")
    assert "Smash Burguer" in resp.text
