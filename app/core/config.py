"""Application settings, loaded once from the environment.

Every secret and every environment-specific value enters the application here and
nowhere else.  Modules import the singleton `settings` rather than reading
`os.environ` themselves, so there is a single place to audit configuration.
"""

import ssl
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The placeholder shipped in .env.example.  Starting a production deployment
# with this value would let anyone mint their own admin token.
INSECURE_SECRET_PLACEHOLDER = "change-me"
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application ------------------------------------------------------
    app_name: str = "Buildify API"
    environment: str = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    # --- Database ---------------------------------------------------------
    mysql_host: str = "mysql"
    mysql_port: int = 3306
    mysql_user: str = "marketplace"
    mysql_password: str = "marketplace"
    mysql_database: str = "marketplace"

    # CA certificate used to verify the database's TLS certificate.  Accepts
    # either an inline PEM block or a path to a .pem file - inline is what a
    # host like Render can actually hold, since it only offers environment
    # variables.  Empty means no TLS, which is right for a database reached
    # over a private network (Docker Compose locally, Railway internally) and
    # wrong for any managed provider reached across the internet.
    #
    # Managed MySQL (Aiven, Cloud SQL, RDS) signs its certificates with the
    # provider's own CA rather than a public one, so the system trust store is
    # not enough - the CA has to be supplied here or verification fails.
    mysql_ssl_ca: str = ""

    # Connection pool.  Small by default: the free tiers this deploys to cap
    # connections hard (Aiven's free MySQL allows 76 in total), and every
    # process holds up to pool_size + max_overflow of them.  Raise on a
    # database that can afford it.
    db_pool_size: int = 5
    db_max_overflow: int = 10

    # --- Security ---------------------------------------------------------
    jwt_secret_key: str = INSECURE_SECRET_PLACEHOLDER
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # Largest request body accepted, before it reaches a route handler.  Guards
    # against a single huge JSON body tying up CPU (Argon2 hashing a multi-MB
    # "password", for instance).
    max_request_bytes: int = 1 * 1024 * 1024

    # --- Rate limiting ----------------------------------------------------
    # On by default in every environment, not just production: a limiter that
    # only switches on in production is one that has never been exercised
    # before the day it matters.  The test suite disables it explicitly.
    rate_limit_enabled: bool = True
    rate_limit_login: str = "20/minute"
    rate_limit_register: str = "10/hour"
    rate_limit_refresh: str = "60/minute"
    # Trust X-Forwarded-For only when actually behind a proxy that rewrites it.
    #
    # Left unset this follows the environment (see `trust_proxy`): on in
    # production, where every supported deploy target (Railway, Render, Fly)
    # terminates TLS at a proxy and an untrusted setting would put every user
    # in one rate-limit bucket; off locally, where nothing rewrites the header
    # and trusting it would let a caller defeat the login rate limit outright
    # by sending a different X-Forwarded-For with each attempt.
    trust_proxy_headers: bool | None = None

    # --- Documentation ----------------------------------------------------
    # Interactive docs are invaluable while building and a free API map for an
    # attacker in production.
    expose_docs: bool | None = None

    # --- CORS -------------------------------------------------------------
    # Comma separated in the environment; parsed by `cors_origin_list`.
    # Kept as a plain string because pydantic-settings would otherwise try to
    # JSON-decode a list field and fail on "a,b" input.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- Cloudflare R2 ----------------------------------------------------
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket: str = "marketplace-media"
    r2_public_base_url: str = ""
    r2_presign_expire_seconds: int = 900
    max_upload_bytes: int = 5 * 1024 * 1024

    # --- Validation -------------------------------------------------------
    @model_validator(mode="after")
    def _reject_insecure_production_config(self) -> "Settings":
        """Refuse to boot a production deployment with unsafe settings.

        Failing loudly at startup is the whole point: a silent fallback to the
        placeholder secret produces a system that looks healthy while anyone
        can forge an admin token for it.
        """
        if not self.is_production:
            return self

        problems: list[str] = []
        if self.jwt_secret_key == INSECURE_SECRET_PLACEHOLDER:
            problems.append("JWT_SECRET_KEY is still the placeholder value")
        if len(self.jwt_secret_key) < MIN_SECRET_LENGTH:
            problems.append(
                f"JWT_SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters"
            )
        if self.debug:
            problems.append("DEBUG must be false in production")
        if "*" in self.cors_origin_list:
            problems.append("CORS_ORIGINS must not contain '*' when credentials are allowed")

        if problems:
            raise ValueError(
                "Refusing to start in production:\n  - "
                + "\n  - ".join(problems)
                + '\n\nGenerate a secret with: python -c "import secrets; '
                'print(secrets.token_urlsafe(48))"'
            )
        return self

    # --- Derived ----------------------------------------------------------
    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def trust_proxy(self) -> bool:
        """Explicit setting if given, otherwise on only in production."""
        if self.trust_proxy_headers is not None:
            return self.trust_proxy_headers
        return self.is_production

    @property
    def docs_enabled(self) -> bool:
        """Explicit setting if given, otherwise off in production."""
        if self.expose_docs is not None:
            return self.expose_docs
        return not self.is_production

    @property
    def database_url(self) -> str:
        """Async URL used by the application at runtime."""
        return (
            f"mysql+asyncmy://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def sync_database_url(self) -> str:
        """Blocking URL used by Alembic, which has no async story worth the trouble."""
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def db_connect_args(self) -> dict:
        """Driver-level connection arguments, shared by the app and Alembic.

        asyncmy and PyMySQL both accept an `ssl.SSLContext` under the same
        `ssl` key - their `_create_ssl_ctx` short-circuits on one - so a single
        context serves the async runtime engine and the synchronous migration
        engine without a second code path.
        """
        ca = self.mysql_ssl_ca.strip()
        if not ca:
            return {}

        # create_default_context() already means "verify the chain and check
        # the hostname"; loading the provider CA on top is what makes that
        # chain resolvable.  Neither setting is relaxed here on purpose: TLS
        # without verification encrypts the traffic while leaving it open to
        # anyone who can answer on that address, which is the attack a hosted
        # database over the public internet actually faces.
        context = ssl.create_default_context()
        if ca.startswith("-----BEGIN"):
            context.load_verify_locations(cadata=ca)
        else:
            context.load_verify_locations(cafile=ca)
        return {"ssl": context}

    @property
    def r2_endpoint_url(self) -> str:
        return f"https://{self.r2_account_id}.r2.cloudflarestorage.com"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
