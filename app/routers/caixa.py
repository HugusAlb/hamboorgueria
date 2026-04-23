from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models import (
    ItemPedido, Movimento, MovimentoCreate, MovimentoRead,
    Pagamento, PagamentoCreate, PagamentoRead,
    Pedido, Produto, StatusPedido,
)

router = APIRouter(prefix="/caixa", tags=["caixa"])


@router.get("/pedido/{pedido_id}/total")
def calcular_total(pedido_id: int, session: Session = Depends(get_session)):
    pedido = session.get(Pedido, pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    itens = session.exec(select(ItemPedido).where(ItemPedido.pedido_id == pedido_id)).all()
    total = sum(
        item.quantidade * session.get(Produto, item.produto_id).preco
        for item in itens
    )
    return {"pedido_id": pedido_id, "total": round(total, 2)}


@router.post("/pedido/{pedido_id}/pagar", response_model=PagamentoRead, status_code=201)
def registrar_pagamento(
    pedido_id: int,
    dados: PagamentoCreate,
    session: Session = Depends(get_session),
):
    pedido = session.get(Pedido, pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    if pedido.pagamento:
        raise HTTPException(status_code=409, detail="Pedido já foi pago")

    pagamento = Pagamento(pedido_id=pedido_id, **dados.model_dump())
    session.add(pagamento)

    pedido.status = StatusPedido.entregue
    session.add(pedido)

    movimento = Movimento(
        tipo="entrada",
        descricao=f"Pagamento pedido #{pedido_id} ({dados.forma})",
        valor=dados.valor_pago,
    )
    session.add(movimento)

    session.commit()
    session.refresh(pagamento)
    return pagamento
