from fastapi.testclient import TestClient
from sqlmodel import Session
from app.models import Produto


def _produto(session: Session, nome="X-Burguer", preco=25.0, disponivel=True) -> Produto:
    p = Produto(nome=nome, preco=preco, disponivel=disponivel)
    session.add(p)
    session.commit()
    session.refresh(p)
    return p


def test_criar_produto(client: TestClient):
    resp = client.post("/admin/produtos", data={"nome": "Fritas", "preco": "12.50"})
    assert resp.status_code == 200
    assert "Fritas" in resp.text
    assert "12.50" in resp.text


def test_editar_produto(client: TestClient, session: Session):
    p = _produto(session)
    resp = client.patch(
        f"/admin/produtos/{p.id}",
        data={"nome": "X-Bacon", "preco": "32.00", "disponivel": "true"},
    )
    assert resp.status_code == 200
    assert "X-Bacon" in resp.text
    assert "32.00" in resp.text


def test_apagar_produto(client: TestClient, session: Session):
    p = _produto(session, nome="Onion Rings")
    resp = client.delete(f"/admin/produtos/{p.id}")
    assert resp.status_code == 200
    assert "Onion Rings" not in resp.text


def test_toggle_disponibilidade_para_esgotado(client: TestClient, session: Session):
    p = _produto(session, disponivel=True)
    resp = client.patch(f"/admin/produtos/{p.id}/disponibilidade")
    assert resp.status_code == 200
    assert "Esgotado" in resp.text


def test_toggle_disponibilidade_para_disponivel(client: TestClient, session: Session):
    p = _produto(session, disponivel=False)
    resp = client.patch(f"/admin/produtos/{p.id}/disponibilidade")
    assert resp.status_code == 200
    assert "Disponível" in resp.text


def test_produto_esgotado_nao_aparece_no_cliente(client: TestClient, session: Session):
    _produto(session, nome="Sundae", disponivel=False)
    resp = client.get("/cliente")
    assert resp.status_code == 200
    assert "Sundae" not in resp.text


def test_formulario_edicao_renderiza(client: TestClient, session: Session):
    p = _produto(session, nome="Smash")
    resp = client.get(f"/admin/produtos/{p.id}/editar")
    assert resp.status_code == 200
    assert "Smash" in resp.text
    assert 'name="nome"' in resp.text
