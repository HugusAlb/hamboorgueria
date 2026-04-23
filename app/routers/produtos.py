from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from app.database import get_session
from app.models import Produto, ProdutoCreate, ProdutoRead

router = APIRouter(prefix="/produtos", tags=["produtos"])


@router.get("/", response_model=list[ProdutoRead])
def listar_produtos(session: Session = Depends(get_session)):
    return session.exec(select(Produto)).all()


@router.post("/", response_model=ProdutoRead, status_code=201)
def criar_produto(dados: ProdutoCreate, session: Session = Depends(get_session)):
    produto = Produto.model_validate(dados)
    session.add(produto)
    session.commit()
    session.refresh(produto)
    return produto


@router.patch("/{produto_id}", response_model=ProdutoRead)
def atualizar_produto(
    produto_id: int,
    dados: ProdutoCreate,
    session: Session = Depends(get_session),
):
    produto = session.get(Produto, produto_id)
    if not produto:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    for campo, valor in dados.model_dump(exclude_unset=True).items():
        setattr(produto, campo, valor)
    session.add(produto)
    session.commit()
    session.refresh(produto)
    return produto


@router.delete("/{produto_id}", status_code=204)
def deletar_produto(produto_id: int, session: Session = Depends(get_session)):
    produto = session.get(Produto, produto_id)
    if not produto:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    session.delete(produto)
    session.commit()
