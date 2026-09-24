"""M68 — identificação assistida no cadastro: CPF local primeiro, SERPRO depois.

Ordem das portas (cada uma só abre se a anterior deixou passar):

1. CPF válido pela função canônica (`services/cpf.py`);
2. CPF EXATO no banco local → paciente já cadastrado; o SERPRO nem é
   consultado (não se paga para confirmar quem já está aqui);
3. integração configurada (`Settings.serpro_cpf_ready`) — senão, a tela
   segue no preenchimento manual;
4. resultado recente em cache para o mesmo par → devolvido sem nova chamada;
5. limite por usuário numa janela deslizante (anti-custo / anti-abuso);
6. só então, uma consulta externa.

Privacidade:

* o cache e o limitador guardam só um HMAC do par CPF+nascimento com chave
  aleatória do processo — nenhum CPF em claro fica em memória de controle;
* o resultado vive só na memória do processo (nunca no banco), e só com os
  campos que a tela mostra;
* o "comprovante" devolvido à tela é um HMAC com validade curta que permite,
  na hora de salvar, registrar SE o nome veio confirmado e SE foi editado —
  sem gravar CPF, nascimento ou nome na auditoria.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import date

from ..config import Settings, get_settings
from ..normalize import normalize_name
from .serpro_cpf import ConsultaCpf, SerproCpfClient

CACHE_TTL_DEFINITIVO = 15 * 60   # confere / não confere / recusado / protegido
CACHE_TTL_INDISPONIVEL = 60      # falha técnica: tenta de novo só depois
CACHE_MAX_ITENS = 1000
COMPROVANTE_VALIDADE = 2 * 3600  # o operador pode demorar a salvar
COMPROVANTE_VERSAO = "v1"

_CHAVE_PROCESSO = secrets.token_bytes(32)


def chave_do_par(cpf: str, nascimento: date) -> str:
    """HMAC do par com chave efêmera do processo: serve de chave de cache
    sem que o cache contenha o CPF."""

    msg = f"{cpf}|{nascimento.isoformat()}".encode()
    return hmac.new(_CHAVE_PROCESSO, msg, hashlib.sha256).hexdigest()


class CacheConsultas:
    def __init__(self, clock=time.monotonic, max_itens: int = CACHE_MAX_ITENS):
        self._clock = clock
        self._max = max_itens
        self._itens: OrderedDict[str, tuple[float, ConsultaCpf]] = OrderedDict()
        self._lock = threading.Lock()

    def obter(self, chave: str) -> ConsultaCpf | None:
        with self._lock:
            item = self._itens.get(chave)
            if item is None:
                return None
            expira, valor = item
            if self._clock() >= expira:
                self._itens.pop(chave, None)
                return None
            return valor

    def guardar(self, chave: str, valor: ConsultaCpf) -> None:
        ttl = CACHE_TTL_INDISPONIVEL if valor.resultado == "indisponivel" else CACHE_TTL_DEFINITIVO
        with self._lock:
            self._itens[chave] = (self._clock() + ttl, valor)
            self._itens.move_to_end(chave)
            while len(self._itens) > self._max:
                self._itens.popitem(last=False)

    def limpar(self) -> None:
        with self._lock:
            self._itens.clear()


class LimiteConsultas:
    """Janela deslizante por usuário (id técnico, nunca PII)."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._eventos: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def permitir(self, user_id: str, maximo: int, janela_segundos: float) -> bool:
        agora = self._clock()
        with self._lock:
            fila = self._eventos.setdefault(user_id, deque())
            while fila and agora - fila[0] >= janela_segundos:
                fila.popleft()
            if len(fila) >= maximo:
                return False
            fila.append(agora)
            return True

    def limpar(self) -> None:
        with self._lock:
            self._eventos.clear()


cache_consultas = CacheConsultas()
limite_consultas = LimiteConsultas()

# ------------------------------------------------------------ provider

_provider_lock = threading.Lock()
_provider: SerproCpfClient | None = None
_provider_assinatura: tuple | None = None


def get_consulta_cpf_provider() -> SerproCpfClient | None:
    """Dependência FastAPI: o cliente, ou None quando não configurado.

    Um único cliente por processo para que o Bearer seja reaproveitado
    (o token vive só aqui, em memória). Testes substituem esta dependência.
    """

    global _provider, _provider_assinatura
    settings: Settings = get_settings()
    if not settings.serpro_cpf_ready():
        return None
    assinatura = (
        hashlib.sha256(
            (settings.serpro_cpf_consumer_key.get_secret_value() + "\0"
             + settings.serpro_cpf_consumer_secret.get_secret_value()).encode()
        ).hexdigest(),
        settings.serpro_cpf_timeout_seconds,
    )
    with _provider_lock:
        if _provider is None or _provider_assinatura != assinatura:
            _provider = SerproCpfClient(
                consumer_key=settings.serpro_cpf_consumer_key.get_secret_value().strip(),
                consumer_secret=settings.serpro_cpf_consumer_secret.get_secret_value().strip(),
                timeout_seconds=settings.serpro_cpf_timeout_seconds,
            )
            _provider_assinatura = assinatura
        return _provider


# ------------------------------------------------------------ comprovante

def _chave_comprovante(settings: Settings) -> bytes:
    return hashlib.sha256(
        b"m68-identificacao-oficial|" + settings.resolved_auth_secret().encode()
    ).digest()


def _mac(chave: bytes, texto: str) -> str:
    return hmac.new(chave, texto.encode(), hashlib.sha256).hexdigest()[:32]


def emitir_comprovante(
    cpf: str, nascimento: date, nome_oficial: str, settings: Settings | None = None,
    agora: float | None = None,
) -> str:
    settings = settings or get_settings()
    chave = _chave_comprovante(settings)
    expira = int((agora if agora is not None else time.time()) + COMPROVANTE_VALIDADE)
    base = f"{cpf}|{nascimento.isoformat()}|{expira}"
    mac_par = _mac(chave, "par|" + base)
    mac_nome = _mac(chave, f"nome|{base}|{normalize_name(nome_oficial)}")
    return f"{COMPROVANTE_VERSAO}.{expira}.{mac_par}.{mac_nome}"


def avaliar_comprovante(
    comprovante: str | None, cpf: str | None, nascimento: date | None,
    nome_salvo: str, settings: Settings | None = None, agora: float | None = None,
) -> str | None:
    """Código para a auditoria, ou None quando não houve comprovante.

    - ``serpro_confirmado``: o nome salvo é o nome oficial devolvido;
    - ``serpro_nome_alterado``: o par confere, mas o operador editou o nome;
    - ``comprovante_invalido``: expirado, adulterado ou de outro CPF/nascimento.
    """

    if not comprovante:
        return None
    partes = comprovante.split(".")
    if len(partes) != 4 or partes[0] != COMPROVANTE_VERSAO or not cpf or not nascimento:
        return "comprovante_invalido"
    try:
        expira = int(partes[1])
    except ValueError:
        return "comprovante_invalido"
    if (agora if agora is not None else time.time()) > expira:
        return "comprovante_invalido"
    settings = settings or get_settings()
    chave = _chave_comprovante(settings)
    base = f"{cpf}|{nascimento.isoformat()}|{expira}"
    if not hmac.compare_digest(_mac(chave, "par|" + base), partes[2]):
        return "comprovante_invalido"
    if hmac.compare_digest(_mac(chave, f"nome|{base}|{normalize_name(nome_salvo)}"), partes[3]):
        return "serpro_confirmado"
    return "serpro_nome_alterado"


# ------------------------------------------------------------ banco local

def pessoa_por_cpf(db, cpf: str):
    """Busca EXATA em `people.cpf` (coluna única e indexada). Sem LIKE."""

    from sqlalchemy import select

    from ..models import Person

    return db.execute(select(Person).where(Person.cpf == cpf)).scalar_one_or_none()


def pessoa_minima(person) -> dict:
    """O que a tela mostra para "Paciente já cadastrado" — nada além disso."""

    return {
        "id": person.id,
        "public_code": person.public_code,
        "nome_completo": person.nome_completo,
        "data_nascimento": person.data_nascimento.isoformat()
        if person.data_nascimento else None,
    }
