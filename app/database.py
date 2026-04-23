from sqlalchemy import event, text
from sqlmodel import SQLModel, create_engine, Session, select

DATABASE_URL = "sqlite:///./hamboorgueria.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(conn, _):
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA cache_size=10000")
    cur.execute("PRAGMA temp_store=MEMORY")
    cur.close()


def create_db():
    SQLModel.metadata.create_all(engine)
    _migrar_pedido()
    _migrar_codigo_pedido()
    _limpar_financeiro()
    seed_cliente_demo()
    with engine.connect() as conn:
        try:
            conn.execute(text("ALTER TABLE cliente ADD COLUMN senha_hash TEXT"))
            conn.commit()
        except Exception:
            pass


def _migrar_pedido():
    with engine.connect() as conn:
        colunas = [r[1] for r in conn.execute(text("PRAGMA table_info(pedido)")).fetchall()]
        if "tipo_entrega" in colunas:
            return  # já migrado

        conn.execute(text("PRAGMA foreign_keys=off"))
        conn.execute(text("""
            CREATE TABLE pedido_novo (
                id          INTEGER NOT NULL PRIMARY KEY,
                mesa        INTEGER,
                tipo_entrega VARCHAR NOT NULL DEFAULT 'local',
                observacao  VARCHAR,
                status      VARCHAR(10) NOT NULL,
                criado_em   DATETIME NOT NULL,
                cliente_id  INTEGER REFERENCES cliente(id)
            )
        """))
        conn.execute(text("""
            INSERT INTO pedido_novo (id, mesa, tipo_entrega, observacao, status, criado_em, cliente_id)
            SELECT id, mesa, 'local', observacao, status, criado_em, cliente_id FROM pedido
        """))
        conn.execute(text("DROP TABLE pedido"))
        conn.execute(text("ALTER TABLE pedido_novo RENAME TO pedido"))
        conn.execute(text("PRAGMA foreign_keys=on"))
        conn.commit()


def _migrar_codigo_pedido():
    from app.models import gerar_codigo_pedido

    with engine.connect() as conn:
        colunas = [r[1] for r in conn.execute(text("PRAGMA table_info(pedido)")).fetchall()]
        if "codigo" not in colunas:
            conn.execute(text("ALTER TABLE pedido ADD COLUMN codigo VARCHAR"))
            conn.commit()

        rows = conn.execute(text("SELECT id FROM pedido WHERE codigo IS NULL")).fetchall()
        for (pid,) in rows:
            codigo = gerar_codigo_pedido()
            while conn.execute(text("SELECT 1 FROM pedido WHERE codigo = :c"), {"c": codigo}).fetchone():
                codigo = gerar_codigo_pedido()
            conn.execute(text("UPDATE pedido SET codigo = :c WHERE id = :id"), {"c": codigo, "id": pid})
        if rows:
            conn.commit()


def _limpar_financeiro():
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM pagamento"))
        conn.execute(text("DELETE FROM movimento"))
        conn.commit()


def seed_cliente_demo():
    from app.models import Cliente, hash_senha

    with Session(engine) as session:
        if not session.exec(select(Cliente).where(Cliente.telefone == "00000000000")).first():
            session.add(Cliente(
                nome="Demo",
                telefone="00000000000",
                senha_hash=hash_senha("demo123"),
            ))
            session.commit()


def seed_usuarios():
    from app.models import Usuario, hash_senha

    defaults = [
        ("caixa",     "caixa123",    "caixa"),
        ("cozinha",   "cozinha123",  "cozinha"),
        ("auditoria", "adm123",      "auditoria"),
    ]
    with Session(engine) as session:
        for login, senha, perfil in defaults:
            if not session.exec(select(Usuario).where(Usuario.login == login)).first():
                session.add(Usuario(login=login, senha_hash=hash_senha(senha), perfil=perfil))
        session.commit()


def get_session():
    with Session(engine) as session:
        yield session
