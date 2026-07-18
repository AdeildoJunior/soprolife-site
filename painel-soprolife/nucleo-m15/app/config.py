"""Configuração por variáveis de ambiente (prefixo M15_). Fail-closed.

Regras de produção (M15_ENV=prod):
- M15_AUTH_SECRET obrigatório, >=32 caracteres e >=10 símbolos distintos;
- bind deve ser loopback, salvo consentimento explícito via
  M15_ALLOW_NONLOCAL_BIND=eu-entendo-o-risco;
- CORS apenas com origens http(s) explícitas — nunca "*".
"""

import secrets
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
MIN_SECRET_LEN = 32
MIN_SECRET_DISTINCT = 10
TTL_MIN_MINUTES = 5
TTL_MAX_MINUTES = 720
NONLOCAL_BIND_CONSENT = "eu-entendo-o-risco"


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
    allow_nonlocal_bind: str = ""
    cors_origins: list[str] = ["http://127.0.0.1:8765", "http://localhost:8765"]
    display_timezone: str = "America/Sao_Paulo"

    @field_validator("token_ttl_minutes")
    @classmethod
    def _ttl_em_faixa(cls, v: int) -> int:
        if not (TTL_MIN_MINUTES <= v <= TTL_MAX_MINUTES):
            raise ValueError(
                f"M15_TOKEN_TTL_MINUTES deve estar entre {TTL_MIN_MINUTES} e "
                f"{TTL_MAX_MINUTES} minutos."
            )
        return v

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
            if (
                self.api_host not in LOOPBACK_HOSTS
                and self.allow_nonlocal_bind != NONLOCAL_BIND_CONSENT
            ):
                raise ValueError(
                    "Em prod, M15_API_HOST deve ser loopback. Bind público exige "
                    f"M15_ALLOW_NONLOCAL_BIND={NONLOCAL_BIND_CONSENT} (consciente)."
                )
            for origin in self.cors_origins:
                parsed = urlsplit(origin)
                if parsed.scheme != "https" and parsed.hostname not in LOOPBACK_HOSTS:
                    raise ValueError(
                        "Em prod, CORS não-loopback exige HTTPS; HTTP só é aceito "
                        "para origem local."
                    )
        return self

    def resolved_auth_secret(self) -> str:
        if self.auth_secret:
            return self.auth_secret
        # dev sem segredo: efêmero por processo (tokens caem a cada restart)
        if not hasattr(self, "_ephemeral_secret"):
            object.__setattr__(self, "_ephemeral_secret", secrets.token_hex(32))
        return self._ephemeral_secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
