import hashlib
import secrets
import string
from datetime import datetime
from enum import Enum
from typing import Optional
from sqlmodel import Field, Relationship, SQLModel


def gerar_codigo_pedido() -> str:
    chars = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(6))


def hash_senha(senha: str, salt: str | None = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt.encode(), 100_000)
    return f"{salt}:{dk.hex()}"


def verificar_senha(senha: str, hash_armazenado: str) -> bool:
    try:
        salt, _ = hash_armazenado.split(":", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_senha(senha, salt), hash_armazenado)


class StatusPedido(str, Enum):
    pendente  = "pendente"
    aguardando = "aguardando"
    preparando = "preparando"
    pronto = "pronto"
    entregue = "entregue"
    cancelado = "cancelado"


class TipoEntrega(str, Enum):
    retirar = "retirar"
    local   = "local"


class FormaPagamento(str, Enum):
    dinheiro = "dinheiro"
    cartao_debito = "cartao_debito"
    cartao_credito = "cartao_credito"
    pix = "pix"


class TipoMovimento(str, Enum):
    entrada = "entrada"
    saida = "saida"


class StatusSolicitacao(str, Enum):
    pendente = "pendente"
    aprovado = "aprovado"
    rejeitado = "rejeitado"


# --- Usuario (staff) ---

class Usuario(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    login: str = Field(unique=True, index=True)
    senha_hash: str
    perfil: str  # "caixa" | "cozinha" | "auditoria"
    token: Optional[str] = Field(default=None, unique=True, index=True)


# --- Cliente ---

class ClienteBase(SQLModel):
    nome: str
    telefone: str = Field(unique=True, index=True)


class Cliente(ClienteBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(
        default_factory=lambda: secrets.token_urlsafe(32),
        unique=True, index=True,
    )
    senha_hash: Optional[str] = Field(default=None)
    pedidos: list["Pedido"] = Relationship(back_populates="cliente")


class ClienteRead(ClienteBase):
    id: int


# --- Produto ---

class ProdutoBase(SQLModel):
    nome: str = Field(index=True)
    preco: float
    disponivel: bool = True


class Produto(ProdutoBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    itens: list["ItemPedido"] = Relationship(back_populates="produto")


class ProdutoCreate(ProdutoBase):
    pass


class ProdutoRead(ProdutoBase):
    id: int


# --- Pedido ---

class PedidoBase(SQLModel):
    mesa: Optional[int] = None
    tipo_entrega: TipoEntrega = TipoEntrega.local
    observacao: Optional[str] = None


class Pedido(PedidoBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    codigo: str = Field(default_factory=gerar_codigo_pedido, unique=True, index=True)
    status: StatusPedido = StatusPedido.aguardando
    criado_em: datetime = Field(default_factory=datetime.utcnow)
    cliente_id: Optional[int] = Field(default=None, foreign_key="cliente.id")
    cliente: Optional["Cliente"] = Relationship(back_populates="pedidos")
    itens: list["ItemPedido"] = Relationship(back_populates="pedido")
    pagamento: Optional["Pagamento"] = Relationship(back_populates="pedido")
    solicitacoes: list["SolicitacaoEdicao"] = Relationship(back_populates="pedido")


class PedidoCreate(PedidoBase):
    pass


class PedidoRead(PedidoBase):
    id: int
    status: StatusPedido
    criado_em: datetime


# --- Item do Pedido ---

class ItemPedidoBase(SQLModel):
    quantidade: int = Field(ge=1)
    observacao: Optional[str] = None


class ItemPedido(ItemPedidoBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    pedido_id: int = Field(foreign_key="pedido.id")
    produto_id: int = Field(foreign_key="produto.id")
    pedido: Optional[Pedido] = Relationship(back_populates="itens")
    produto: Optional[Produto] = Relationship(back_populates="itens")


class ItemPedidoCreate(ItemPedidoBase):
    produto_id: int


class ItemPedidoRead(ItemPedidoBase):
    id: int
    produto_id: int
    produto: Optional[ProdutoRead] = None


# --- Pagamento ---

class PagamentoBase(SQLModel):
    forma: FormaPagamento
    valor_pago: float


class Pagamento(PagamentoBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    pedido_id: int = Field(foreign_key="pedido.id", unique=True)
    registrado_em: datetime = Field(default_factory=datetime.utcnow)
    pedido: Optional[Pedido] = Relationship(back_populates="pagamento")


class PagamentoCreate(PagamentoBase):
    pass


class PagamentoRead(PagamentoBase):
    id: int
    pedido_id: int
    registrado_em: datetime


# --- Solicitação de Edição ---

class SolicitacaoEdicao(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    pedido_id: int = Field(foreign_key="pedido.id")
    itens_antes: str  # JSON serializado
    itens_depois: str  # JSON serializado
    obs_antes: Optional[str] = None
    obs_depois: Optional[str] = None
    status: StatusSolicitacao = StatusSolicitacao.pendente
    criado_em: datetime = Field(default_factory=datetime.utcnow)
    pedido: Optional[Pedido] = Relationship(back_populates="solicitacoes")


# --- Movimento (auditoria) ---

class MovimentoBase(SQLModel):
    tipo: TipoMovimento
    descricao: str
    valor: float


class Movimento(MovimentoBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    registrado_em: datetime = Field(default_factory=datetime.utcnow)


class MovimentoCreate(MovimentoBase):
    pass


class MovimentoRead(MovimentoBase):
    id: int
    registrado_em: datetime
