# Basics
from typing import AsyncGenerator, List, Optional, Literal, Sequence, Self, Dict
import secrets
from cryptography.fernet import Fernet
import base64
import hashlib
import json

# Libs
import enum
import uuid
from pydantic import SecretStr
from sqlmodel import Field, Column, Enum, UniqueConstraint, CheckConstraint
from passlib.context import CryptContext
import secrets
import datetime

# Internal
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.model._base_model import TimestampedModel, BaseTable
from checkcheckserver.model.user import User


log = get_logger()
config = Config()

# Passwords benefit from slow hashing algorithms like bcrypt or argon2 to resist brute-force attacks.
crypt_context_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

# API keys, being long and random, don't need slow hashing—so faster algorithms like sha256_crypt or pbkdf2_sha256 may be sufficient and more efficient.
crypt_context_api_token = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def _generate_fernet_key(input_str: str) -> bytes:
    """
    Deterministically generates a Fernet key from an arbitrary input string.

    Args:
        input_str (str): The input string to derive the key from.

    Returns:
        bytes: A base64-encoded 32-byte key suitable for Fernet.
    """
    # Step 1: Hash the input string to a 32-byte digest
    digest = hashlib.sha256(input_str.encode()).digest()

    # Step 2: Base64-encode the digest to make it Fernet-compatible
    fernet_key = base64.urlsafe_b64encode(digest)

    return fernet_key


fernet = Fernet(_generate_fernet_key(config.AUTH_OIDC_TOKEN_STORAGE_SECRET))


class AllowedAuthSchemeType(str, enum.Enum):
    basic = "basic"
    oidc = "oidc"
    api_token = "api_token"


# UserAuth Models and Table
class _UserAuthBase(BaseTable, table=False):
    user_id: uuid.UUID = Field(foreign_key="user.id")
    auth_source_type: AllowedAuthSchemeType = Field(
        default=AllowedAuthSchemeType.basic,
        sa_column=Column(Enum(AllowedAuthSchemeType)),
    )

    oidc_provider_slug: Optional[str] = Field(index=True, default=None)
    api_token_id: Optional[str] = Field(
        default=None,
        description="A non hashed/encrypted clear text identifier that is attached to the hashed token. This makes it easier to look up the hased token later",
    )
    api_token_source_user_auth_id: Optional[uuid.UUID] = Field(
        default=None,
        description="The UserAuth that was used to create the api_token. This is used to revoke the token if the parent AuthSource is becoming invalid.",
    )
    expires_at_epoch_time: Optional[int] = Field(
        description="A local password can be attached an experation date after it which the password does not work anymore. If the UserAuth is an oidc token, this is the access_tokens expiration date.",
        default=None,
    )
    scope: Optional[str] = Field(
        default=None, description="Placeholder for future use."
    )
    revoked: Optional[bool] = Field(
        default=False, description="Is the authentikation valid or revoked."
    )


class UserAuthUpdate(BaseTable, table=False):
    basic_password: Optional[SecretStr] = Field(
        default=None,
        min_length=10,
        description="The password of the user. Can be None if user is authorized by external provider. e.g. OIDC",
    )
    oidc_token: Optional[dict] = Field(default=None)
    expires_at_epoch_time: Optional[int] = Field(
        description="A local password can be attached an experation date after it which the password does not work anymore. If the UserAuth is an oidc token, this is the access_tokens expiration date.",
        default=None,
    )


class UserAuthCreate(_UserAuthBase, UserAuthUpdate, table=False):
    oidc_provider_slug: Optional[str] = None
    api_token: Optional[SecretStr] = Field(
        default=None,
        min_length=40,
        description="A token to authenticate against the API. Can be used for machine-to-machine API calls. it will be hashed and not re-callable from the database.",
    )

    def generate_api_token(self):
        self.api_token = secrets.token_urlsafe(40)
        self.api_token_id = secrets.token_urlsafe(12)

    def get_api_token(self):
        return f"{self.api_token_id}.{self.api_token}"


class UserAuth(_UserAuthBase, TimestampedModel, table=True):
    """This table stores the information of what type a certain user is and how the user can access our application.
    Either a user is of "local"-type and can login with the (hashed) password in this table or a user is external.
    External user are only comming from a OpenID Connect Provider at the moment and are maked as "oidc". Later there maybe "ldap" user as well.
    External users may have an extra table to store further auth informations. For oidc users that table is in checkcheckserver/db/user_auth_external_oidc_token.py

    Args:
        _UserAuthBase (_type_): _description_
        table (bool, optional): _description_. Defaults to True.

    Raises:
        raise_exception_if_wrong_pw: _description_

    Returns:
        _type_: _description_
    """

    __tablename__ = "user_auth"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False,
        unique=True,
        # sa_column_kwargs={"server_default": text("gen_random_uuid()")},
    )
    basic_password_hashed: Optional[str] = Field(
        default=None,
        description="The hashed password of the user.",
    )
    api_token_hashed: Optional[str] = Field(
        default=None,
        description="The hashed api token.",
    )
    salt: str = Field(
        default_factory=lambda: secrets.token_hex(8),
        description="Salt for hashing basic passwords and api tokens",
    )
    oidc_token_encrypted: Optional[str] = Field(default=None)

    @classmethod
    def from_update_or_create_object(
        cls: "UserAuth", input_obj: UserAuthCreate | UserAuthUpdate, user_id=None
    ) -> Self:
        if not hasattr(input_obj, "user_id"):
            raise ValueError(
                f"from_update_or_create_object with a UserAuthUpdate needs to be called with a user_id)"
            )
        input_obj_raw: Dict = input_obj.model_dump(
            exclude=["basic_password", "api_token", "oidc_token"]
        )
        if user_id:
            input_obj_raw = input_obj_raw | {"user_id": user_id}
        result_obj: UserAuth = UserAuth.model_validate(input_obj_raw)
        result_obj.update_secrets(input_obj)
        return result_obj

    def update_secrets(self, input_obj: UserAuthCreate | UserAuthUpdate) -> Self:
        if self.auth_source_type == AllowedAuthSchemeType.basic:
            self.set_password(input_obj.basic_password)
        elif self.auth_source_type == AllowedAuthSchemeType.api_token:
            self.set_api_token(input_obj.api_token)
            self.expires_at_epoch_time = input_obj.expires_at_epoch_time
        elif self.auth_source_type == AllowedAuthSchemeType.oidc:
            self.set_oidc_token(input_obj.oidc_token)
            self.expires_at_epoch_time = input_obj.expires_at_epoch_time

    def set_password(self, password_unencrypted: str | SecretStr):
        pw = password_unencrypted
        if isinstance(pw, SecretStr):
            pw = password_unencrypted.get_secret_value()
        self.basic_password_hashed = crypt_context_pwd.hash(
            self.add_salt(
                pw,
                self.salt,
            )
        )

    def set_api_token(self, token_unencrypted: str | SecretStr):
        token = token_unencrypted
        if isinstance(token, SecretStr):
            token = token_unencrypted.get_secret_value()
        self.api_token_hashed = crypt_context_api_token.hash(
            self.add_salt(
                token,
                self.salt,
            )
        )

    def verify_password(
        self, password: SecretStr | str, raise_exception_if_wrong: Exception = None
    ) -> bool:
        pw = password
        if isinstance(password, SecretStr):
            pw = password.get_secret_value()
        pw = self.add_salt(pw, self.salt)
        password_correct = crypt_context_pwd.verify(pw, self.basic_password_hashed)
        if not password_correct and raise_exception_if_wrong:
            raise raise_exception_if_wrong
        return password_correct

    def verify_api_token(
        self,
        api_token: SecretStr | str | None,
        raise_exception_if_wrong: Exception | None = None,
    ) -> bool:
        if api_token is None:
            return False
        elif isinstance(api_token, SecretStr):
            token: str = api_token.get_secret_value()
        else:
            token = api_token
        log.debug(f"TOKEN {token}")
        if "." in token:
            token = token.split(".", maxsplit=1)[1]  # remove the token id
        token = self.add_salt(token, self.salt)
        token_correct = crypt_context_api_token.verify(token, self.api_token_hashed)
        if not token_correct and raise_exception_if_wrong:
            log.debug("Token verification failed")
            raise raise_exception_if_wrong
        return token_correct

    def set_oidc_token(self, oidc_token: dict):
        """_summary_

        Args:
            oidc_token (dict): {'access_token': 'zsJdwKVSiyGniMBZrGLPZ3r34oqLEAH3SB1GR8RvJt', 'expires_in': 3600, 'id_token': 'eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc....CwX_n25I3YcA', 'refresh_token': 'MLEPBZTHhujkxtb6jxKhrvy2DOpuKPJaqiYRrKl2cmpr8WFY', 'scope': 'openid profile email', 'token_type': 'Bearer', 'expires_at': 1747088696, 'userinfo': {'iss': 'http://localhost:8884', 'aud': ['devdummyid1345'], 'iat': 1747085096, 'exp': 1747088696, 'auth_time': 1747085096, 'nonce': 'H6NTlQYElTLIJs5xS3qR', 'at_hash': 'vYyQAMXUeMQWkiGd2T6Umw', 'sub': 'admin', 'userinfo': {'name': 'admin', 'email': 'admin@test.com', 'given_name': 'Admin Maier', 'groups': ['medlog-admins', 'others', 'icecream-lovers']}}}
        """
        self.oidc_token_encrypted = fernet.encrypt(
            json.dumps(oidc_token).encode()
        ).decode()

    def update_oidc_token(self, oidc_token: Dict):
        """_summary_

        Args:
            oidc_token (Dict): example `{'access_token': 'pnAkhmYYMwyApZCRhDKx41Mvb3daCrOOAlqbICHhzm', 'expires_in': 3600, 'scope': 'openid profile email', 'token_type': 'Bearer', 'expires_at': 1747088292}`
        """
        old_token = self.get_decrypted_oidc_token()
        old_token = (
            old_token | oidc_token
        )  # merge with old token to not overwrite refresh tokens, on access token update
        self.expires_at_epoch_time = oidc_token["expires_at"]
        self.set_oidc_token(old_token)

    def get_decrypted_oidc_token(self) -> dict:
        return json.loads(fernet.decrypt(self.oidc_token_encrypted).decode())

    def is_expired(self, leeway_sec: int = 30):
        if self.revoked:
            return True
        if self.expires_at_epoch_time is None:
            return False
        return (
            self.expires_at_epoch_time
            < datetime.datetime.now(tz=datetime.UTC).timestamp() + leeway_sec
        )

    @classmethod
    def add_salt(cls, pw: str, salt: str):
        return f"{pw}{salt}"
