"""Partnership center: bounded read projections over the existing sources."""
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session
from ..db import get_db
from ..models import User
from ..security import ROLE_GESTOR, ROLE_LEITURA, require_role, user_effective_roles
from ..services import partnership_center as service

router=APIRouter(prefix='/parcerias/central',tags=['central de parcerias'])
Code=Literal['pastore','maclin','alergocare']


def _read(response):
    response.headers['Cache-Control']='private, no-store'
    response.headers['Vary']='Cookie, Authorization'


@router.get('')
def overview(response:Response, inicio:date|None=None, fim:date|None=None,
             parceiro:Literal['todas','pastore','maclin','alergocare']='todas',
             db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_LEITURA))):
    _read(response)
    data,_=service.project(db,ROLE_GESTOR in user_effective_roles(user),inicio,fim,parceiro)
    return data


@router.get('/{codigo}')
def detail(codigo:Code,response:Response,inicio:date|None=None,fim:date|None=None,
           pagina:int=Query(1,ge=1,le=1000),limite:int=Query(20,ge=1,le=50),
           db:Session=Depends(get_db),user:User=Depends(require_role(ROLE_LEITURA))):
    _read(response)
    data,rows=service.project(db,ROLE_GESTOR in user_effective_roles(user),inicio,fim,codigo)
    items=rows[codigo];start=(pagina-1)*limite
    return {**data,'itens':items[start:start+limite],'pagina':pagina,'total':len(items),
            'tem_proxima':start+limite<len(items)}
