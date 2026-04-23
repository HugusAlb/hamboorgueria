from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func
from app.database import get_session
from app.models import Movimento, MovimentoCreate, MovimentoRead, TipoMovimento

router = APIRouter(prefix="/auditoria", tags=["auditoria"])


@router.get("/movimentos", response_model=list[MovimentoRead])
def listar_movimentos(session: Session = Depends(get_session)):
    return session.exec(select(Movimento).order_by(Movimento.registrado_em.desc())).all()


@router.post("/movimentos", response_model=MovimentoRead, status_code=201)
def registrar_movimento(dados: MovimentoCreate, session: Session = Depends(get_session)):
    movimento = Movimento.model_validate(dados)
    session.add(movimento)
    session.commit()
    session.refresh(movimento)
    return movimento


@router.get("/resumo")
def resumo(session: Session = Depends(get_session)):
    def total_por_tipo(tipo: TipoMovimento) -> float:
        resultado = session.exec(
            select(func.sum(Movimento.valor)).where(Movimento.tipo == tipo)
        ).first()
        return round(resultado or 0.0, 2)

    entradas = total_por_tipo(TipoMovimento.entrada)
    saidas = total_por_tipo(TipoMovimento.saida)
    return {
        "entradas": entradas,
        "saidas": saidas,
        "saldo": round(entradas - saidas, 2),
    }
