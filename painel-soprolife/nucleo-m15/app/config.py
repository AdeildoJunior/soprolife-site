"""Configuração por variáveis de ambiente (prefixo M15_). Fail-closed.

Regras de produção (M15_ENV=prod):
- M15_AUTH_SECRET obrigatório, >=32 caracteres e >=10 símbolos distintos;
- bind deve ser sempre loopback;
- CORS apenas com origens http(s) explícitas — nunca "*";
- cookie de sessão sempre Secure (M21) — HTTPS não é negociável em prod.
"""

import os
import secrets
import stat
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
MIN_SECRET_LEN = 32
MIN_SECRET_DISTINCT = 10
TTL_MIN_MINUTES = 5
TTL_MAX_MINUTES = 720

# Sessão de navegador (M21). A duração é ajustável por configuração, mas o
# teto persistente é fixo em 7 dias: nenhuma variável de ambiente pode
# transformar "manter conectado" em credencial eterna.
SESSION_MIN_MINUTES = 5
SESSION_MAX_MINUTES = 720          # sem "manter conectado" (morre com o navegador)
SESSION_PERSISTENT_MAX_DAYS = 7    # com "manter conectado" — teto absoluto


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="M15_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    env: Literal["dev", "prod"] = "dev"
    database_url: str = "sqlite:///./var/m15_nucleo.db"
    auth_secret: str | None = None
    token_ttl_minutes: int = 120
    api_host: str = "127.0.0.1"
    api_port: int = 8015
    cors_origins: list[str] = ["http://127.0.0.1:8765", "http://localhost:8765"]
    display_timezone: str = "America/Sao_Paulo"

    # -------------------------------------- preço de tabela SoproLife (M25.26)
    # Valor com que o campo "Valor da espirometria" NASCE preenchido no fluxo
    # de Espirometria SoproLife. É uma SUGESTÃO editável, nunca um valor
    # imposto: o operador apaga ou troca antes de salvar, e o que vale é o que
    # ficou no campo.
    #
    # Mora aqui, e não numa constante em JavaScript, porque preço muda por
    # decisão comercial. Espalhado em arquivos de tela, um reajuste vira uma
    # caçada por números soltos e sobra um 220 esquecido em algum lugar
    # criando lançamento com o preço velho.
    #
    # NÃO é usado para inferir valor nenhum no servidor: o financeiro continua
    # nascendo só de valor explícito no payload (regra do M20). Se o campo
    # chegar vazio, nenhum lançamento é criado — a ausência permanece ausência.
    espirometria_soprolife_valor_padrao: Decimal = Decimal("220.00")

    # ------------------------------------------- sessão de navegador (M21)
    # Cookie assinado, HttpOnly, SameSite=Strict, Path restrito ao prefixo
    # público da API. Nunca guarda o token bearer nem a senha.
    session_cookie_name: str = "soprolife_m15_sessao"
    # M25.23 — alargado de "/painel-soprolife/api/m15" para "/painel-soprolife".
    #
    # O escopo antigo fazia o navegador enviar o cookie SÓ para a API. Com ele,
    # a camada estática do painel era estruturalmente incapaz de saber quem
    # estava pedindo a página — e foi exatamente por isso que ela servia o
    # conteúdo restrito a qualquer um. O gate de boot depende de reconhecer a
    # sessão em GET /painel-soprolife/ e em /painel-soprolife/data/*.
    #
    # O que NÃO mudou: HttpOnly, SameSite=strict e Secure (obrigatório em prod).
    # O cookie continua preso ao painel — nunca vaza para o site institucional
    # na mesma origem — e o proxy segue filtrando por allowlist de nome antes
    # de repassar qualquer cookie à API.
    session_cookie_path: str = "/painel-soprolife"
    # Em prod o validador abaixo força True; em dev loopback (http) o padrão
    # False permite desenvolver sem TLS sem jamais afrouxar produção.
    session_cookie_secure: bool = False
    session_ttl_minutes: int = 720               # sessão de navegador
    session_persistent_days: int = 7             # "manter conectado" (teto 7)
    # Pedido manual de Marketing. Contém apenas timestamp/origem, fica no
    # diretório privado e gravável da API; o timer consome o mesmo caminho.
    marketing_refresh_queue: Path = Path("./var/marketing-refresh-request.json")

    # M24D — contrato explícito de três estados para o piloto controlado de
    # laudos. "disabled" (padrão) e "production" nunca servem a API de
    # laudos: production permanece bloqueada porque não existe assinatura
    # qualificada nem aprovação jurídica/clínica (ver
    # scripts/reports_go_live_gate.py). Apenas "pilot" pode operar, e mesmo
    # assim só quando M15_REPORTS_ENABLED=true também estiver presente — a
    # variável geral do M15 sozinha nunca é suficiente.
    reports_mode: Literal["disabled", "pilot", "production"] = "disabled"
    # M24A permanece independente do restante do Núcleo M15 e desabilitado
    # por padrão. Um deploy de código ou a ativação global do M15 nunca
    # habilita a API de laudos por efeito colateral.
    reports_enabled: bool = False
    # M24A — raiz de armazenamento dos PDFs de laudo (original + versões
    # geradas). NUNCA dentro do Git, nunca dentro de um diretório de
    # snapshot público. Sem valor: o serviço de laudos falha fechado (não
    # existe default dentro do repositório). Validado de verdade em
    # `resolved_reports_storage_dir()`, chamado só quando a feature de
    # laudos é usada — não trava o resto da API se M24A não estiver em uso.
    reports_storage_dir: Path | None = None
    # Tamanho máximo aceito para um PDF enviado (bytes). 25 MiB cobre um
    # laudo de espirometria com imagens de curva sem abrir espaço para
    # abuso de armazenamento.
    reports_max_upload_bytes: int = 25 * 1024 * 1024
    # Exclusivo para fixtures sintéticas em desenvolvimento. O runtime
    # normal nunca permite selecionar templates provisórios. Em produção o
    # validador abaixo recusa até mesmo a tentativa de ligar esta chave.
    reports_test_allow_provisional_templates: bool = False
    # M25.2 — base pública do endereço de validação impresso no laudo (texto
    # + QR Code). Sem valor configurado o laudo sai apenas com o código de
    # verificação textual: nenhuma URL é inventada. Precisa ser HTTPS.
    reports_validation_base_url: str | None = None
    # Tamanho máximo do PNG de assinatura manuscrita (bytes).
    reports_signature_max_bytes: int = 2 * 1024 * 1024

    # ------------------------------------------ M25.7 — VIDaaS/IntegraICP
    #
    # Assinatura QUALIFICADA ICP-Brasil pelo certificado em nuvem da médica.
    # Tudo fail-closed: sem `report_signature_provider="integraicp"` E
    # `integraicp_enabled=True` E as três configurações obrigatórias abaixo,
    # a fábrica devolve o provedor nulo e o caminho qualificado permanece
    # inalcançável — exatamente como antes desta etapa.
    #
    # A liberação institucional (assinatura eletrônica interna) NUNCA depende
    # destas variáveis: ela continua funcionando com a integração desligada.
    report_signature_provider: Literal["unconfigured", "integraicp"] = "unconfigured"
    integraicp_enabled: bool = False
    # Sem valor padrão de propósito: endpoint, canal e callback são dados de
    # contrato com a Valid e nunca podem estar escritos no repositório.
    integraicp_base_url: str | None = None
    integraicp_channel_id: str | None = None
    integraicp_callback_url: str | None = None
    # Política de assinatura CMS (OID ou identificador acordado com a AC).
    integraicp_signature_policy: str | None = None
    # Timeouts finitos: uma chamada pendurada trava a médica na tela.
    integraicp_request_timeout_seconds: float = 20.0
    # Janela em que a credencial devolvida pelo callback continua utilizável.
    integraicp_credential_lifetime_seconds: int = 300
    # Janela total do clearance: da solicitação até a autorização no app.
    integraicp_clearance_lifetime_seconds: int = 600

    @field_validator("integraicp_base_url", "integraicp_callback_url")
    @classmethod
    def _integraicp_https(cls, value: str | None) -> str | None:
        """Base e callback só podem ser HTTPS.

        O callback carrega o CredentialId; o base URL recebe o digest. Nenhum
        dos dois pode trafegar em claro, nem mesmo em homologação.
        """

        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        if not normalized.startswith("https://"):
            raise ValueError(
                "M15_INTEGRAICP_BASE_URL e M15_INTEGRAICP_CALLBACK_URL "
                "precisam ser URLs HTTPS."
            )
        return normalized

    def integraicp_ready(self) -> bool:
        """Integração utilizável de verdade — sem isso, nada é chamado.

        Não basta `enabled=True`: sem base, canal e callback a integração
        está incompleta, e uma tentativa de uso viraria uma chamada a um
        endpoint indefinido. Fail-closed: na dúvida, não está pronta.
        """

        return bool(
            self.integraicp_enabled
            and self.report_signature_provider == "integraicp"
            and self.integraicp_base_url
            and self.integraicp_channel_id
            and self.integraicp_callback_url
        )

    @field_validator("reports_validation_base_url")
    @classmethod
    def _validation_url_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        if not normalized.startswith("https://"):
            raise ValueError(
                "M15_REPORTS_VALIDATION_BASE_URL precisa ser uma URL HTTPS."
            )
        return normalized

    @field_validator("token_ttl_minutes")
    @classmethod
    def _ttl_em_faixa(cls, v: int) -> int:
        if not (TTL_MIN_MINUTES <= v <= TTL_MAX_MINUTES):
            raise ValueError(
                f"M15_TOKEN_TTL_MINUTES deve estar entre {TTL_MIN_MINUTES} e "
                f"{TTL_MAX_MINUTES} minutos."
            )
        return v

    @field_validator("session_ttl_minutes")
    @classmethod
    def _sessao_em_faixa(cls, v: int) -> int:
        if not (SESSION_MIN_MINUTES <= v <= SESSION_MAX_MINUTES):
            raise ValueError(
                f"M15_SESSION_TTL_MINUTES deve estar entre {SESSION_MIN_MINUTES} "
                f"e {SESSION_MAX_MINUTES} minutos."
            )
        return v

    @field_validator("session_persistent_days")
    @classmethod
    def _persistente_em_faixa(cls, v: int) -> int:
        if not (1 <= v <= SESSION_PERSISTENT_MAX_DAYS):
            raise ValueError(
                "M15_SESSION_PERSISTENT_DAYS deve estar entre 1 e "
                f"{SESSION_PERSISTENT_MAX_DAYS} dias."
            )
        return v

    @field_validator("session_cookie_name")
    @classmethod
    def _nome_cookie_valido(cls, v: str) -> str:
        if not v or any(c in v for c in ' ;,="\\\t\r\n'):
            raise ValueError("M15_SESSION_COOKIE_NAME contém caracteres inválidos.")
        return v

    @field_validator("session_cookie_path")
    @classmethod
    def _path_cookie_valido(cls, v: str) -> str:
        if not v.startswith("/") or any(c in v for c in ' ;,"\\\t\r\n'):
            raise ValueError(
                "M15_SESSION_COOKIE_PATH deve ser um caminho absoluto simples."
            )
        return v.rstrip("/") or "/"

    @field_validator("cors_origins")
    @classmethod
    def _cors_explicito(cls, origins: list[str]) -> list[str]:
        if not origins:
            raise ValueError("M15_CORS_ORIGINS não pode ser vazio.")
        for origin in origins:
            if origin == "*" or not origin.startswith(("http://", "https://")):
                raise ValueError(
                    "M15_CORS_ORIGINS exige origens http(s) explícitas; '*' é proibido."
                )
            parsed = urlsplit(origin)
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.path not in ("", "/")
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("M15_CORS_ORIGINS contém origem inválida.")
        return origins

    @model_validator(mode="after")
    def _regras_de_prod(self) -> "Settings":
        if self.env == "prod":
            secret = self.auth_secret or ""
            if len(secret) < MIN_SECRET_LEN or len(set(secret)) < MIN_SECRET_DISTINCT:
                raise ValueError(
                    "Em prod, M15_AUTH_SECRET precisa de >=32 caracteres e "
                    ">=10 símbolos distintos. Gere com: "
                    "python3 -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if self.api_host not in LOOPBACK_HOSTS:
                raise ValueError(
                    "Em prod, M15_API_HOST deve ser loopback; bind público é proibido."
                )
            for origin in self.cors_origins:
                parsed = urlsplit(origin)
                if parsed.scheme != "https" and parsed.hostname not in LOOPBACK_HOSTS:
                    raise ValueError(
                        "Em prod, CORS não-loopback exige HTTPS; HTTP só é aceito "
                        "para origem local."
                    )
            # M21 — em produção o cookie de sessão é SEMPRE Secure. Não há
            # variável de ambiente capaz de desligar isso.
            object.__setattr__(self, "session_cookie_secure", True)
            if self.reports_test_allow_provisional_templates:
                raise ValueError(
                    "M15_REPORTS_TEST_ALLOW_PROVISIONAL_TEMPLATES é proibido em prod."
                )
        return self

    def resolved_auth_secret(self) -> str:
        if self.auth_secret:
            return self.auth_secret
        # dev sem segredo: efêmero por processo (tokens caem a cada restart)
        if not hasattr(self, "_ephemeral_secret"):
            object.__setattr__(self, "_ephemeral_secret", secrets.token_hex(32))
        return self._ephemeral_secret

    def resolved_reports_storage_dir(self) -> Path:
        """Raiz de armazenamento de laudos PDF — fail-closed.

        Chamado sob demanda pelo serviço de laudos (não no boot da API
        inteira), mas SEMPRE antes de qualquer leitura/escrita de arquivo.
        Recusa: ausente, caminho relativo, dentro da árvore de trabalho do
        Git (repositório), qualquer componente symlink ou modo com acesso de
        grupo/outros. Resolve ancestrais antes da checagem de contenção,
        cria cada componente ausente com modo efetivo 0700 e repete todas as
        pós-condições depois da criação.
        """
        if not self.reports_storage_dir:
            raise ValueError(
                "M15_REPORTS_STORAGE_DIR não configurado — armazenamento de "
                "laudos recusado (fail-closed)."
            )
        raw = Path(self.reports_storage_dir)
        if not raw.is_absolute():
            raise ValueError(
                "M15_REPORTS_STORAGE_DIR deve ser um caminho absoluto."
            )
        _assert_no_symlink_components(raw)

        # `strict=False` resolve todos os ancestrais existentes mesmo quando
        # o componente final ainda não existe. Isso fecha o escape em que um
        # ancestral symlink apontava para dentro do worktree.
        resolved_before_creation = raw.resolve(strict=False)
        repo_root = _find_git_repo_root()
        _assert_outside_git_worktree(resolved_before_creation, repo_root)

        _create_private_directory_chain(resolved_before_creation)

        # Pós-condições repetidas depois de mkdir: uma troca concorrente por
        # symlink, um modo efetivo permissivo ou um escape via resolução
        # interrompe a operação. Nenhum chmod/mkdir/stat é ignorado.
        _assert_no_symlink_components(resolved_before_creation)
        resolved_after_creation = resolved_before_creation.resolve(strict=True)
        _assert_outside_git_worktree(resolved_after_creation, repo_root)
        _assert_private_directory(resolved_after_creation)
        return resolved_after_creation


def _find_git_repo_root() -> Path | None:
    """Sobe a árvore de diretórios a partir deste arquivo até achar `.git`."""
    for parent in Path(__file__).resolve().parents:
        if (parent / ".git").exists():
            return parent
    return None


def _assert_outside_git_worktree(path: Path, repo_root: Path | None) -> None:
    if repo_root is None:
        return
    try:
        path.relative_to(repo_root.resolve(strict=True))
    except ValueError:
        return
    raise ValueError(
        "M15_REPORTS_STORAGE_DIR não pode estar dentro do repositório Git."
    )


def _assert_no_symlink_components(path: Path) -> None:
    """Recusa qualquer symlink existente na cadeia lexical do caminho."""
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current = current / component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(
                "M15_REPORTS_STORAGE_DIR não pode conter symlink."
            )


def _assert_private_directory(path: Path) -> None:
    mode = path.stat().st_mode
    if not stat.S_ISDIR(mode):
        raise ValueError(
            "M15_REPORTS_STORAGE_DIR precisa apontar para um diretório."
        )
    if stat.S_IMODE(mode) & 0o077:
        raise ValueError(
            "M15_REPORTS_STORAGE_DIR possui permissões de grupo/outros."
        )


def _create_private_directory_chain(path: Path) -> None:
    """Cria todos os componentes ausentes com modo efetivo 0700.

    `Path.mkdir(parents=True, mode=...)` aplica `mode` apenas à folha e
    depende do umask para os pais. Aqui cada componente recebe fchmod/chmod
    explícito e é verificado antes de o próximo ser criado.
    """
    missing: list[Path] = []
    cursor = path
    while True:
        try:
            mode = cursor.lstat().st_mode
        except FileNotFoundError:
            missing.append(cursor)
            parent = cursor.parent
            if parent == cursor:
                raise ValueError(
                    "Não foi possível localizar um ancestral do armazenamento."
                )
            cursor = parent
            continue
        if stat.S_ISLNK(mode):
            raise ValueError(
                "M15_REPORTS_STORAGE_DIR não pode conter symlink."
            )
        if not stat.S_ISDIR(mode):
            raise ValueError(
                "Ancestral do armazenamento não é um diretório."
            )
        break

    for directory in reversed(missing):
        os.mkdir(directory, 0o700)
        os.chmod(directory, 0o700)
        _assert_private_directory(directory)

    # Uma raiz preexistente também precisa chegar já privada; não tentamos
    # "consertar" silenciosamente uma configuração insegura.
    _assert_private_directory(path)


@lru_cache
def get_settings() -> Settings:
    return Settings()
