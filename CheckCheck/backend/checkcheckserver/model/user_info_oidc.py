from typing import (
    AsyncGenerator,
    List,
    Optional,
    Literal,
    Sequence,
    Annotated,
    Self,
    Dict,
)

from pydantic import BaseModel, Field

from checkcheckserver.utils import get_value_from_first_dict_with_key
from checkcheckserver.config import Config
from checkcheckserver.log import get_logger


log = get_logger()
config = Config()


class UserInfoOidc(BaseModel):
    provider_slug: str
    sub: str
    email: str
    _preferred_username: Optional[str] = None
    _name: Optional[str] = None
    groups: Optional[List[str]] = Field(default_factory=list)

    @property
    def preferred_username(self):
        return self._preferred_username if self._preferred_username else self.sub

    @property
    def name(self):
        return self._name if self._name else self.preferred_username

    @classmethod
    def from_raw_userinfo(
        cls, raw_userinfo: Dict, oidc_config: Config.OpenIDConnectProvider
    ) -> Self:
        if "userinfo" in raw_userinfo:
            # in some cases secondary attributes are nested in a inner userinfo dict
            secondary_raw_userinfo = raw_userinfo["userinfo"]
            raw_userinfo = [raw_userinfo, secondary_raw_userinfo]
        else:
            raw_userinfo = [raw_userinfo]
        sub = get_value_from_first_dict_with_key(raw_userinfo, "sub", None)
        email = get_value_from_first_dict_with_key(
            raw_userinfo, oidc_config.USER_MAIL_ATTRIBUTE, None
        )
        userinfo = UserInfoOidc(
            provider_slug=oidc_config.get_provider_name_slug(), sub=sub, email=email
        )
        userinfo._preferred_username = get_value_from_first_dict_with_key(
            raw_userinfo, oidc_config.USER_NAME_ATTRIBUTE, None
        )
        userinfo._name = get_value_from_first_dict_with_key(
            raw_userinfo, oidc_config.USER_DISPLAY_NAME_ATTRIBUTE, None
        )
        userinfo.groups = get_value_from_first_dict_with_key(
            raw_userinfo, oidc_config.USER_GROUPS_ATTRIBUTE, list()
        )
        return userinfo
