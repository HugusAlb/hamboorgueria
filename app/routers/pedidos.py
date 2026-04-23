from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models import (
    ItemPedido, ItemPedidoCreate, ItemPedidoRead,
    Pedido, PedidoCreate, PedidoRead,
    Produto, StatusPedido,
)

router = APIRouter(prefix="/pedidos", tags=["pedidos"])


@router.get("/", response_model=list[PedidoRead])
def listar_pedidos(session: Session = Depends(get_session)):
    return session.exec(select(Pedido)).all()


@router.get("/{pedido_id}", response_model=PedidoRead)
def obter_pedido(pedido_id: int, session: Session = Depends(get_session)):
    pedido = session.get(Pedido, pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return pedido


@router.post("/", response_model=PedidoRead, status_code=201)
def criar_pedido(dados: PedidoCreate, session: Session = Depends(get_session)):
    pedido = Pedido.model_validate(dados)
    session.add(pedido)
    session.commit()
    session.refresh(pedido)
    return pedido


@router.patch("/{pedido_id}/status", response_model=PedidoRead)
def atualizar_status(
    pedido_id: int,
    status: StatusPedido,
    session: Session = Depends(get_session),
):
    pedido = session.get(Pedido, pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    pedido.status = status
    session.add(pedido)
    session.commit()
    session.refresh(pedido)
    return pedido


@router.get("/{pedido_id}/itens", response_model=list[ItemPedidoRead])
def listar_itens(pedido_id: int, session: Session = Depends(get_session)):
    pedido = session.get(Pedido, pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return session.exec(select(ItemPedido).where(ItemPedido.pedido_id == pedido_id)).all()


@router.post("/{pedido_id}/itens", response_model=ItemPedidoRead, status_code=201)
def adicionar_item(
    pedido_id: int,
    dados: ItemPedidoCreate,
    session: Session = Depends(get_session),
):
    pedido = session.get(Pedido, pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    produto = session.get(Produto, dados.produto_id)
    if not produto:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    item = ItemPedido(pedido_id=pedido_id, **dados.model_dump())
    session.add(item)
    session.commit()
    session.refresh(item)
    return item
