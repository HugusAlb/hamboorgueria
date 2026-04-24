import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

from app.main import app, _ratelimit
from app.database import get_session
from app.models import Usuario, hash_senha


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    _ratelimit.clear()
    yield


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture(name="client")
def client_fixture(session: Session):
    def override():
        yield session

    app.dependency_overrides[get_session] = override
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


@pytest.fixture(name="auditoria_client")
def auditoria_client_fixture(client: TestClient, session: Session):
    session.add(Usuario(login="auditoria", senha_hash=hash_senha("adm123"), perfil="auditoria"))
    session.commit()
    client.post("/login", data={"login": "auditoria", "senha": "adm123", "next": "/"})
    return client


@pytest.fixture(name="caixa_client")
def caixa_client_fixture(client: TestClient, session: Session):
    session.add(Usuario(login="caixa", senha_hash=hash_senha("caixa123"), perfil="caixa"))
    session.commit()
    client.post("/login", data={"login": "caixa", "senha": "caixa123", "next": "/"})
    return client


@pytest.fixture(name="cozinha_client")
def cozinha_client_fixture(client: TestClient, session: Session):
    session.add(Usuario(login="cozinha", senha_hash=hash_senha("coz123"), perfil="cozinha"))
    session.commit()
    client.post("/login", data={"login": "cozinha", "senha": "coz123", "next": "/"})
    return client
