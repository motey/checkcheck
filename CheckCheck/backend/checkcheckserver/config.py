from pydantic_settings import BaseSettings, SettingsConfigDict
import os
from pydantic import (
    Field,
    SecretStr,
    StringConstraints,
    model_validator,
)
from typing import List, Annotated, Optional, Literal, Dict
from pathlib import Path, PurePath
import socket
from textwrap import dedent
from checkcheckserver.utils import get_random_string, val_means_true, slugify_string

env_file_path = os.environ.get(
    "CHECKCHECK_DOT_ENV_FILE", Path(__file__).parent / ".env"
)

checkcheckserver_folder = Path(__file__).parent
repo_root_folder = checkcheckserver_folder.parent.parent.parent


class Config(BaseSettings):
    APP_NAME: str = "CheckCheck"
    DOCKER_MODE: bool = False
    FRONTEND_FILES_DIR: str = Field(
        description="The generated nuxt dir that contains index.html,...",
        default="CheckCheck/frontend/.output/public",
    )  # todo: move to new "Static" class
    LOG_LEVEL: Literal["CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"] = Field(
        default="INFO"
    )

    DEBUG_SQL: bool = Field(
        default=False,
        description="If set to true, the sql engine will print out all sql queries to the log.",
    )
    # Webserver
    SERVER_UVICORN_LOG_LEVEL: Optional[str] = Field(
        default=None,
        description="The log level of the uvicorn server. If not defined it will be the same as LOG_LEVEL.",
    )

    SERVER_LISTENING_PORT: int = Field(default=8181)
    SERVER_LISTENING_HOST: str = Field(
        default="localhost",
        examples=["0.0.0.0", "localhost", "127.0.0.1", "176.16.8.123"],
    )
    # ToDo: Read https://fastapi.tiangolo.com/advanced/behind-a-proxy/ if that is of any help for better hostname/FQDN detection
    SERVER_HOSTNAME: Optional[str] = Field(
        default_factory=socket.gethostname,
        description="The (external) hostname/domainname where the API is available. Usally a FQDN in productive systems. If not defined, it will be automatically detected based on the hostname.",
        examples=["mydomain.com", "localhost"],
    )
    SERVER_PROTOCOL: Optional[Literal["http", "https"]] = Field(
        default="http",
        description="The protocol detection can fail in certain reverse proxy situations. This option allows you to manually override the automatic detection",
    )

    SERVER_SESSION_SECRET: SecretStr = Field(
        description="The secret used to encrypt session state. Provide a long random string.",
        min_length=64,
    )
    SET_SESSION_COOKIE_SECURE: bool = Field(
        default=True,
        description="if you want to run the app on a non ssl connection set this to false. e.g for local development.",
    )

    API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES: Optional[int] = Field(
        default=60 * 24 * 7,  # one week
        description="If an api access token was created (on login or in token management) they should expire after this time.",
    )

    def get_server_url(self) -> str:
        if self.SERVER_PROTOCOL is not None:
            proto = self.SERVER_PROTOCOL
        elif self.SERVER_LISTENING_PORT == 443:
            proto = "https"

        port = ""
        if self.SERVER_LISTENING_PORT not in [80, 443]:
            port = f":{self.SERVER_LISTENING_PORT}"
        return f"{proto}://{self.SERVER_HOSTNAME}{port}"

    CLIENT_URL: Optional[str] = Field(default=None, description="Origin url")
    SQL_DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./local.sqlite")

    ADMIN_USER_NAME: str = Field(default="admin")
    ADMIN_USER_PW: SecretStr = Field()
    ADMIN_USER_EMAIL: Optional[str] = Field(default=None)
    ADMIN_ROLE_NAME: str = Field(default="admin")
    USERMANAGER_ROLE_NAME: str = Field(default="usermanager")

    APP_PROVISIONING_DATA_YAML_FILES: Optional[List[str]] = Field(
        default_factory=list,
        description="A list if yaml files to serialize and load into CheckCheck models and into the DB ",
    )

    APP_PROVISIONING_DEFAULT_DATA_YAML_FILE: str = Field(
        description="Default data like some background jobs and vocabulary that is always loaded in the database. Under normal circustances this is nothing you need to changed. if you need to provision data like a Study into the database use the APP_PROVISIONING_DATA_YAML_FILES param.",
        default=str(Path(checkcheckserver_folder, "default_data.yaml")),
    )
    NEW_USER_DEFAULT_LABELS: Optional[List[str]] = Field(
        default=["Work", "Private", "Inspiration"]
    )

    AUTH_BASIC_LOGIN_IS_ENABLED: bool = Field(
        default=True,
        description="Local DB users are enabled to login. You could disable this, when having an external OIDC provider.",
    )

    AUTH_BASIC_USER_DB_REGISTER_ENABLED: Literal[False] = Field(
        default=False, description="Self registration of users is not supported yet."
    )

    AUTH_BASIC_SESSION_LIFETIME_MINUTES: Optional[int] = Field(
        default=60 * 24 * 14,  # 2 weeks
        description=dedent(
            """User need to relogin after this amount of time
            """
        ),
    )

    AUTH_JWT_SECRET: SecretStr = Field(
        description="The secret used to sign the JWT tokens. Provide a long random string.",
        min_length=64,
    )
    AUTH_JWT_ALGORITHM: Literal["HS256"] = Field(
        default="HS256",
        description="The algorithm used to sign the JWT tokens. Only HS256 is supported atm",
    )
    AUTH_ACCESS_TOKEN_EXPIRES_MINUTES: Optional[int] = Field(
        default=60 * 24 * 14,
        description=dedent(
            """User need to relogin after this amount of time
            """
        ),
        deprecated=True,
    )

    class OpenIDConnectProvider(BaseSettings):
        ENABLED: bool = Field(default=False, description="Is the provider enabled")
        PROVIDER_DISPLAY_NAME: str = Field(
            description="The unique name of the OpenID Connect provider shown to the user.",
            default="My OpenID Connect Login",
        )

        def get_provider_name_slug(self):
            return slugify_string(self.PROVIDER_DISPLAY_NAME)

        AUTO_LOGIN: Optional[bool] = Field(
            default=False,
            description="If set to true, the client will try to immediatly redirect to this provider instead of showing the login page.",
        )
        CONFIGURATION_ENDPOINT: str = Field(
            description="The discovery endpoint of the OpenID Connect provider."
        )
        CLIENT_ID: str = Field(
            description="The client id of the OpenID Connect provider."
        )
        CLIENT_SECRET: SecretStr = Field(
            description="The client secret of the OpenID Connect provider."
        )
        SCOPES: List[str] = Field(
            description="", default=["openid", "profile", "email"]
        )

        def get_scopes_as_string(self):
            return " ".join(self.SCOPES)

        USER_NAME_ATTRIBUTE: str = Field(
            description="The attribute of the OpenID Connect provider that contains a unique id of the user.",
            default="preferred_username",
        )
        USER_DISPLAY_NAME_ATTRIBUTE: str = Field(
            description="The attribute of the OpenID Connect provider that contains the display name of the user.",
            default="display_name",
        )
        USER_MAIL_ATTRIBUTE: str = Field(
            description="The attribute of the OpenID Connect provider that contains a unique id of the user.",
            default="email",
        )
        USER_GROUPS_ATTRIBUTE: str = Field(description="", default="groups")

        AUTO_CREATE_AUTHORIZED_USER: bool = Field(
            default=True,
            description="If a user does not exists in the local database, create the user on first authorization via the OIDC Provider.",
        )
        PREFIX_USERNAME_WITH_PROVIDER_SLUG: bool = Field(
            default=False,
            description="To prevent username colliction between different OIDC providers, we can prefix the usernames from the OIDC provider with it slug.",
        )
        ROLE_MAPPING: Dict[str, List[str]] = Field(
            default_factory=dict,
            description="""A JSON to map OIDC groups to DZDMedLog Roles. e.g. `{"oidc_appadmins":["medlog-user-manager"],"admins":["medlog-admins"]}`""",
        )

    AUTH_OIDC_TOKEN_STORAGE_SECRET: Optional[str] = Field(
        description="Random string to encrypt the oidc access and refresh token for storing it in the database.",
        default="placeholder_until_todo_see_below",
    )  # todo only needed if AUTH_OIDC_PROVIDERS is not empty. Create a model_validation
    AUTH_OIDC_PROVIDERS: Optional[List[OpenIDConnectProvider]] = Field(
        description="Configure additional/alternative OpenID Connect (OIDC) provider settings for integrating.",
        default_factory=list,
    )

    EXPORT_CACHE_DIR: str = Field(
        default="./export_cache",
        description="The directory to store the result of export jobs (CSV files, JSON files,...).",
    )

    DB_MIGRATION_ALEMBIC_CONFIG_FILE: str = Field(
        default=f"{repo_root_folder}/alembic.ini"
    )

    ###### CONFIG END ######
    # "class Config:" is a pydantic-settings pre-defined config class to control the behaviour of our settings model
    # you could call it a "meta config" class
    # if you dont know what this is you can ignore it.
    # https://docs.pydantic.dev/latest/api/base_model/#pydantic.main.BaseModel.model_config

    class Config:
        env_nested_delimiter = "__"
        env_file = env_file_path
        env_file_encoding = "utf-8"
        extra = "ignore"
