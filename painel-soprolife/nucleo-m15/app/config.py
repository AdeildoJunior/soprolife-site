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

from pydantic import SecretStr, field_validator, model_validator
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


def _validar_origens(origins: list[str], nome: str) -> list[str]:
    """Origens de CORS sempre explícitas — '*' é proibido em qualquer lista."""

    if not origins:
        raise ValueError(f"{nome} não pode ser vazio.")
    for origin in origins:
        if origin == "*" or not origin.startswith(("http://", "https://")):
            raise ValueError(
                f"{nome} exige origens http(s) explícitas; '*' é proibido."
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
            raise ValueError(f"{nome} contém origem inválida.")
    return origins


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

    # Fiscal foundation: only the in-process mock has an implementation.
    nfse_enabled: bool = False
    nfse_environment: Literal["mock", "restricted", "production"] = "mock"
    nfse_real_enabled: bool = False
    nfse_credentials_path: Path | None = None

    # M27 — National NFS-e restricted (homologação) provider foundation.
    #
    # THE central safety switch this milestone exists to add: even with a
    # restricted base URL, a certificate path and `nfse_real_enabled=true`
    # all configured, no operational HTTP request leaves the process unless
    # this is explicitly true.
    #
    # M56 — production now has its OWN switch below. The two are separate
    # fields with separate environment variables, read by separate transport
    # classes: `HttpxRestrictedTransport` only ever reads this one and only
    # ever accepts environment='restricted'; `HttpxProductionTransport` only
    # ever reads the production one and only ever accepts
    # environment='production'. Turning either on cannot turn the other on.
    nfse_restricted_network_enabled: bool = False
    # HTTPS base URL for Produção Restrita. Never a production hostname —
    # this codebase has no code path that would send this URL a real request
    # from a "production" environment value.
    nfse_restricted_base_url: str | None = None
    # PKCS#12 bundle path (never a real certificate committed to the repo).
    # Password lives in `nfse_restricted_certificate_password` — a SEPARATE
    # variable, so a leaked path alone never yields a usable credential.
    nfse_restricted_certificate_path: Path | None = None
    nfse_restricted_certificate_password: str | None = None
    # Single supported restricted layout for this foundation (see
    # app/services/nfse_national/config.py for the evidence trail). A future
    # layout bump adds a new literal value here, never mutates this one.
    nfse_restricted_layout_version: Literal["restricted-v1.01-20260727"] = "restricted-v1.01-20260727"
    # Private root for DPS/NFS-e/event XML and DANFSe artifacts. Same
    # fail-closed contract as `reports_storage_dir`: absent by default, must
    # be absolute, outside the Git worktree, and end up 0700.
    nfse_fiscal_artifacts_dir: Path | None = None

    # M56 — PRODUCTION network gate. Deliberately a separate variable
    # (`M15_NFSE_PRODUCTION_NETWORK_ENABLED`), never a mode of the
    # restricted one: an operator enabling Produção Restrita for a
    # homologation run must not be able to enable production as a side
    # effect, and neither flag is consulted by the other environment's
    # transport. Default False, like every other gate in this foundation.
    #
    # There is NO `nfse_production_base_url`. The production endpoint is the
    # literal constant `nfse_national.transport.PRODUCTION_BASE_URL`,
    # enforced by a host allowlist — so there is no environment variable
    # that could point a production issuance at an arbitrary host.
    nfse_production_network_enabled: bool = False
    # Minimum certificate runway, in whole days, before a PRODUCTION cycle
    # may start (see nfse_national.certificate_guard). Production-only:
    # Produção Restrita keeps its original "not expired" rule, so this can
    # never break a homologation run. The real A1 expires 2026-11-07 and
    # must be renewed before the first production issuance.
    nfse_production_certificate_min_days: int = 30
    # M66 — the production issuance button. The web process NEVER sends: it
    # records a human confirmation and drops a doorbell file (just the
    # request UUID) here, and a separate one-shot systemd worker — the only
    # process that ever sees the A1 — picks it up. Unset means the button is
    # unavailable (fail closed), not "send from the web process".
    nfse_production_worker_spool_dir: Path | None = None
    # Worker-only: where each request's pre-POST intent and raw responses go
    # (0700 dirs, 0600 files, outside Git). The web process never reads it.
    nfse_production_worker_runtime_dir: Path | None = None
    # How long a human confirmation stays usable. Past this the worker
    # refuses it and a new confirmation is required.
    nfse_production_confirmation_ttl_minutes: int = 15

    @field_validator("nfse_restricted_base_url")
    @classmethod
    def _restricted_base_url_https(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        if not normalized.startswith("https://"):
            raise ValueError(
                "M15_NFSE_RESTRICTED_BASE_URL precisa ser uma URL HTTPS."
            )
        return normalized

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

    # ------------------------------------- M26.4 — portal de resultados
    #
    # Superfície PÚBLICA mínima, isolada do Command Center. Desligada por
    # padrão: um deploy de código nunca abre o portal por efeito colateral.
    #
    # Dois segredos DIFERENTES, e é de propósito que sejam dois:
    #   `portal_token_key`    deriva o link do paciente. Vive apenas no
    #                         EnvironmentFile do serviço INTERNO. O processo
    #                         público compara hashes e nunca precisa dela —
    #                         então nunca a recebe.
    #   `portal_session_secret` assina o cookie de sessão do paciente. Vive
    #                         apenas no EnvironmentFile do serviço PÚBLICO,
    #                         e é distinto de `auth_secret`: um cookie
    #                         administrativo não pode virar atalho no portal
    #                         nem o contrário.
    portal_enabled: bool = False
    # Endereço amigável que o paciente vê e reconhece. O token vai no
    # FRAGMENTO (`#t=`), que o navegador nunca envia ao servidor — nem no
    # Referer, nem em log de acesso, nem em proxy.
    portal_public_base_url: str | None = None
    portal_api_host: str = "127.0.0.1"
    portal_api_port: int = 8016
    portal_token_key: str | None = None
    portal_session_secret: str | None = None
    portal_cookie_name: str = "soprolife_resultado"
    # Origens do navegador autorizadas a falar com a API pública. Explícitas,
    # como em `cors_origins`; '*' é recusado pelo mesmo validador.
    portal_cors_origins: list[str] = ["https://soprolife.com.br"]
    # Sessão curta: o paciente baixa e sai. Faixa validada 5..120.
    portal_session_ttl_minutes: int = 30
    # Validade do link externo. Resultado médico é consultado depois; 90 dias
    # é o meio-termo documentado na M26.4. Faixa validada 7..365.
    portal_access_ttl_days: int = 90
    portal_cookie_secure: bool = False

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

    # ------------------------------------ M68 — Consulta CPF v3 (SERPRO)
    #
    # Assistência de digitação no cadastro: CPF + nascimento → nome oficial.
    # Fail-closed: sem `enabled=True` E as duas credenciais, nada sai do
    # servidor e a tela segue no preenchimento manual. Não há variável de
    # URL: o endereço de produção é constante em `services/serpro_cpf.py`
    # (mesmo desenho do NFS-e de produção), e a versão de demonstração nunca
    # é alcançável por configuração. Credenciais NUNCA compartilhadas com o
    # A1 fiscal — são integrações independentes.
    serpro_cpf_enabled: bool = False
    serpro_cpf_consumer_key: SecretStr | None = None
    serpro_cpf_consumer_secret: SecretStr | None = None
    # Timeout finito e curto: é o operador esperando na frente do paciente.
    serpro_cpf_timeout_seconds: float = 8.0
    # Anti-custo: consultas EXTERNAS por usuário numa janela deslizante.
    serpro_cpf_max_consultas_por_usuario: int = 30
    serpro_cpf_janela_minutos: int = 10

    @field_validator("serpro_cpf_timeout_seconds")
    @classmethod
    def _serpro_timeout_finito(cls, v: float) -> float:
        if not 1.0 <= v <= 20.0:
            raise ValueError("M15_SERPRO_CPF_TIMEOUT_SECONDS deve ficar entre 1 e 20.")
        return v

    @field_validator("serpro_cpf_max_consultas_por_usuario")
    @classmethod
    def _serpro_limite_em_faixa(cls, v: int) -> int:
        if not 1 <= v <= 200:
            raise ValueError("M15_SERPRO_CPF_MAX_CONSULTAS_POR_USUARIO deve ficar entre 1 e 200.")
        return v

    @field_validator("serpro_cpf_janela_minutos")
    @classmethod
    def _serpro_janela_em_faixa(cls, v: int) -> int:
        if not 1 <= v <= 120:
            raise ValueError("M15_SERPRO_CPF_JANELA_MINUTOS deve ficar entre 1 e 120.")
        return v

    def serpro_cpf_ready(self) -> bool:
        """Consulta utilizável de verdade — sem isso, nenhuma chamada sai.

        `enabled=True` sem as duas credenciais é configuração incompleta, e
        incompleta é tratada como desligada (mesmo critério da IntegraICP).
        """

        return bool(
            self.serpro_cpf_enabled
            and self.serpro_cpf_consumer_key
            and self.serpro_cpf_consumer_key.get_secret_value().strip()
            and self.serpro_cpf_consumer_secret
            and self.serpro_cpf_consumer_secret.get_secret_value().strip()
        )

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
        return _validar_origens(origins, "M15_CORS_ORIGINS")

    @field_validator("portal_cors_origins")
    @classmethod
    def _portal_cors_explicito(cls, origins: list[str]) -> list[str]:
        return _validar_origens(origins, "M15_PORTAL_CORS_ORIGINS")

    @field_validator("portal_session_ttl_minutes")
    @classmethod
    def _portal_sessao_curta(cls, v: int) -> int:
        if not 5 <= v <= 120:
            raise ValueError(
                "M15_PORTAL_SESSION_TTL_MINUTES deve ficar entre 5 e 120."
            )
        return v

    @field_validator("portal_access_ttl_days")
    @classmethod
    def _portal_validade_do_link(cls, v: int) -> int:
        if not 7 <= v <= 365:
            raise ValueError(
                "M15_PORTAL_ACCESS_TTL_DAYS deve ficar entre 7 e 365."
            )
        return v

    @field_validator("portal_public_base_url")
    @classmethod
    def _portal_base_publica(cls, v: str | None) -> str | None:
        if v is None:
            return None
        parsed = urlsplit(v)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "M15_PORTAL_PUBLIC_BASE_URL deve ser uma URL https simples."
            )
        return v.rstrip("/")

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
            # M26.4 — o portal é público; em prod ele nunca sobe sem HTTPS
            # nem com o processo escutando fora de loopback. Quem termina em
            # HTTPS é o nginx do subdomínio técnico, não este processo.
            if self.portal_api_host not in LOOPBACK_HOSTS:
                raise ValueError(
                    "Em prod, M15_PORTAL_API_HOST deve ser loopback; o portal "
                    "é publicado pelo proxy, nunca por bind direto."
                )
            if self.portal_enabled:
                if not self.portal_public_base_url:
                    raise ValueError(
                        "Portal habilitado exige M15_PORTAL_PUBLIC_BASE_URL."
                    )
                for origin in self.portal_cors_origins:
                    if urlsplit(origin).scheme != "https":
                        raise ValueError(
                            "Em prod, M15_PORTAL_CORS_ORIGINS exige HTTPS."
                        )
            object.__setattr__(self, "portal_cookie_secure", True)
        return self

    def resolved_auth_secret(self) -> str:
        if self.auth_secret:
            return self.auth_secret
        # dev sem segredo: efêmero por processo (tokens caem a cada restart)
        if not hasattr(self, "_ephemeral_secret"):
            object.__setattr__(self, "_ephemeral_secret", secrets.token_hex(32))
        return self._ephemeral_secret

    def resolved_portal_token_key(self) -> str:
        """Chave que DERIVA o link do paciente — só o serviço interno a tem.

        Fail-closed e sem fallback efêmero: um segredo de processo faria
        todos os links morrerem a cada restart, e um paciente com o link no
        WhatsApp veria "acesso inválido" sem que nada tivesse sido revogado.
        """

        return _segredo_forte(
            self.portal_token_key, "M15_PORTAL_TOKEN_KEY"
        )

    def resolved_portal_session_secret(self) -> str:
        """Assina o cookie do paciente. Distinto de `auth_secret`, sempre.

        Reaproveitar o segredo administrativo faria um cookie de painel ser
        aceito pelo portal — e o oposto — só por terem a mesma assinatura.
        A separação aqui é o que torna a fronteira verificável.
        """

        segredo = _segredo_forte(
            self.portal_session_secret, "M15_PORTAL_SESSION_SECRET"
        )
        if self.auth_secret and segredo == self.auth_secret:
            raise ValueError(
                "M15_PORTAL_SESSION_SECRET não pode ser igual a "
                "M15_AUTH_SECRET: o portal público e o painel privado "
                "precisam de assinaturas distintas."
            )
        return segredo

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

    def resolved_fiscal_artifacts_storage_dir(self) -> Path:
        """Raiz privada dos artefatos fiscais (DPS/NFS-e/eventos/DANFSe).

        Mesmo contrato fail-closed de `resolved_reports_storage_dir()`: sem
        valor configurado, nada é lido ou escrito. Caminho independente do
        de laudos — os dois nunca compartilham diretório, para que uma
        política de retenção ou um incidente em um não alcance o outro.
        """
        if not self.nfse_fiscal_artifacts_dir:
            raise ValueError(
                "M15_NFSE_FISCAL_ARTIFACTS_DIR não configurado — armazenamento "
                "de artefatos fiscais recusado (fail-closed)."
            )
        raw = Path(self.nfse_fiscal_artifacts_dir)
        if not raw.is_absolute():
            raise ValueError(
                "M15_NFSE_FISCAL_ARTIFACTS_DIR deve ser um caminho absoluto."
            )
        _assert_no_symlink_components(raw)
        resolved_before_creation = raw.resolve(strict=False)
        repo_root = _find_git_repo_root()
        _assert_outside_git_worktree(resolved_before_creation, repo_root)
        _create_private_directory_chain(resolved_before_creation)
        _assert_no_symlink_components(resolved_before_creation)
        resolved_after_creation = resolved_before_creation.resolve(strict=True)
        _assert_outside_git_worktree(resolved_after_creation, repo_root)
        _assert_private_directory(resolved_after_creation)
        return resolved_after_creation

    def resolved_nfse_restricted_certificate_password(self) -> str:
        """Senha do PKCS#12 restrito. Fail-closed: sem valor, nada é aberto.

        Nunca logada, nunca incluída em mensagem de exceção — quem precisa
        dela é exclusivamente `services/nfse_national/signer.py`, uma única
        vez por assinatura.
        """
        if not self.nfse_restricted_certificate_password:
            raise ValueError(
                "M15_NFSE_RESTRICTED_CERTIFICATE_PASSWORD não configurado."
            )
        return self.nfse_restricted_certificate_password


def _segredo_forte(valor: str | None, nome: str) -> str:
    """Mesma régua do M15_AUTH_SECRET: >=32 caracteres, >=10 distintos."""

    segredo = valor or ""
    if len(segredo) < MIN_SECRET_LEN or len(set(segredo)) < MIN_SECRET_DISTINCT:
        raise ValueError(
            f"{nome} precisa de >=32 caracteres e >=10 símbolos distintos. "
            "Gere com: python3 -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return segredo


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
