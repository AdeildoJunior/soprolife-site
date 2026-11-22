"""Command Center read APIs. No fiscal preparation, messages or business writes."""
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Response
from pydantic import Field
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User
from ..schemas import StrictModel
from ..security import get_current_user
from ..services import command_center as service, command_search

router=APIRouter(prefix='/comando',tags=['centro-de-comando'])
Brand=Literal['soprolife','eeglife']


def private(response:Response):
    response.headers['Cache-Control']='private, no-store'
    response.headers['Vary']='Cookie, Authorization'


class Search(StrictModel):
    marca: Brand='soprolife'
    termo: str=Field(min_length=3,max_length=80)
    limite: int=Field(default=12,ge=1,le=12)


@router.get('/resumo')
def summary(response:Response,marca:Brand='soprolife',operacional:bool=False,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    private(response)
    return service.summary(db,user,marca,operacional)


@router.get('/pendencias')
def pending(response:Response,marca:Brand='soprolife',categoria:str|None=None,
            pagina:int=Query(1,ge=1,le=500),limite:int=Query(20,ge=1,le=50),
            janela:Literal['hoje','atencao','proximos']|None=None,
            db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    private(response)
    return service.pending(db,user,marca,categoria,pagina,limite,janela)


@router.post('/busca')
def search(payload:Search,response:Response,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    private(response)
    return command_search.search(db,user,payload.marca,payload.termo,payload.limite)


class Record(StrictModel):
    marca: Brand='soprolife'
    tipo: Literal['parceria','pessoa','exame','agenda','laudo','eeg_exame','eeg_pessoa','parceiro','unidade','fiscal','contato','acerto','pastore']
    id: UUID


@router.post('/registro')
def record(payload:Record,response:Response,db:Session=Depends(get_db),user:User=Depends(get_current_user)):
    private(response)
    return command_search.lookup(db,user,payload.marca,payload.tipo,str(payload.id))
