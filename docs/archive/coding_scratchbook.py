from typing import AsyncGenerator, List, Optional, Literal, Sequence


def determine_class():
    from pydantic import BaseModel, Field

    class UserBase(BaseModel):
        email: Optional[str] = Field(default=None)

    class UserCreate(UserBase):
        user_name: Optional[str] = Field(default=None)

    u1 = UserCreate(email="bla", user_name="bla2")
    print(isinstance(u1, UserBase))
    print(type(u1) is UserBase)
    print(type(u1) is UserCreate)

    ub1 = UserBase(email="blp")
    u2 = UserCreate(**ub1.model_dump())
    print(u2)


def use_pydantic_emailstr_manually():
    from pydantic import EmailStr, validate_email
    from pydantic_core import PydanticCustomError

    from email_validator.exceptions_types import EmailNotValidError, EmailSyntaxError

    try:
        validate_email("rightmail@domain.com")
        validate_email("wrongmail.domain.com")
    except PydanticCustomError as er:
        print(er.message())
        print("EMAIL NOT GUT")

    use_pydantic_emailstr_manually()


def test_pzn_cleaner():
    def clean_pzn(pzn: str):
        return pzn.lstrip("PZN").replace("-", "").replace(" ", "")

    def test_clean_pzn():
        test_values = [
            ("PZN12345678", "12345678"),
            ("PZN-98765432", "98765432"),
            ("PZN - 55555555", "55555555"),
            ("PZN - 123-456-789", "123456789"),  # Keine führende "PZN-" entfernen
            ("PZN - ", ""),  # Keine Ziffern vorhanden
            ("123456789", "123456789"),  # Kein führendes "PZN" vorhanden
            ("PZN-11112222", "11112222"),  # Entfernt führendes "-PZN-"
            ("PZN - 3333-444-555", "3333444555"),  # Kein führendes "PZN-" entfernen
            ("PZN", ""),  # Keine Ziffern vorhanden
            ("PZN - 9876 - 5432", "98765432"),  # Kein führendes "PZN-" entfernen
        ]

        for input_value, expected_output in test_values:
            result = clean_pzn(input_value)
            assert (
                result == expected_output
            ), f"Error for input '{input_value}': Expected '{expected_output}', but got '{result}'."

    # Run the test
    test_clean_pzn()


def pathcombiner():

    from typing import List
    from pathlib import Path, PurePath

    def to_path(
        *args: str | Path | PurePath, absolute: bool = True, expanduser: bool = True
    ) -> Path:
        result_path_fragments: List[Path] = []
        for arg in args:
            if isinstance(arg, str):
                result_path_fragments.append(Path(arg))
            elif isinstance(arg, PurePath):
                result_path_fragments.append(arg)
            elif isinstance(arg, PurePath):
                result_path_fragments.append(Path(arg))
        result_path = Path.joinpath(*result_path_fragments)
        if expanduser:
            result_path = result_path.expanduser()
        if absolute:
            result_path = result_path.absolute()
        return result_path

    print(to_path("~", Path("test"), Path("thing/"), "filename.txt"))


def sqlmodel_class_field_extradata():
    # Sondercodes
    import uuid
    from sqlmodel import Field, SQLModel
    from sqlalchemy import String, Integer, Column, SmallInteger

    def custom_field(*args, source_file_csv_index=None, **kwargs):
        # Modify kwargs to include your custom parameters
        source_file_csv_index = kwargs.pop("source_file_csv_index", None)

        fieldinfo = Field(*args, **kwargs)
        print(type(fieldinfo))
        fieldinfo.source_file_csv_index = source_file_csv_index
        # Pass the modified kwargs to the sqlmodel.Field constructor
        return fieldinfo

    class Sondercodes(SQLModel, table=True):
        __tablename__ = "drug_sonder"
        gkvai_source_csv_filename: str = "sonder.txt"
        dateiversion: str = Field(
            description="Dateiversion",
            sa_type=String(3),
            sa_column_kwargs={"comment": "gkvai_source_csv_col_index:0"},
            primary_key=True,
        )
        datenstand: str = Field(
            description="Monat Datenstand (JJJJMM)",
            sa_type=String(6),
            sa_column_kwargs={"comment": "gkvai_source_csv_col_index:1"},
            primary_key=True,
        )
        pzn: str = Field(
            description="Pharmazentralnummer",
            sa_column_kwargs={"comment": "gkvai_source_csv_col_index:3"},
            sa_type=String(8),
            primary_key=True,
        )

    print(Sondercodes)
    for name, field in Sondercodes.model_fields.items():
        csv_source = None
        try:
            csv_source = int(
                getattr(field, "sa_column_kwargs", None)["comment"].split(":")[1]
            )
        except TypeError:
            pass
        print("csv_source", type(csv_source), csv_source)


# sqlmodel_class_field_extradata()
def seperate_string():
    import shlex

    def separate_words_with_quotes(input_string: str):
        return shlex.split(input_string)
        # Define a regular expression pattern to match quoted and non-quoted substrings
        pattern = re.compile(r"((\"[^\"]+\")|(-[^\s]+))")

        # Use findall to extract all matched substrings
        result = pattern.findall(input_string)

        return result

    # Example usage:
    input_string = "hello world i am 'Tom Maier' ok"
    result_list = separate_words_with_quotes(input_string)
    print(result_list)


# print(seperate_string())
def pydanticUNset():
    from pydantic import BaseModel, Field

    class MyClass(BaseModel):
        id: Optional[int] = Field(default="bla")
        name: str = Field()

    myobj = MyClass(name="Hello")
    print(myobj.model_dump(exclude_unset=True))


def dynamic_pageparam_order_by():
    import inspect
    from pydantic import Field, BaseModel
    import fastapi
    from fastapi import Depends, Query
    from typing import Generic, TypeVar, get_args, Annotated
    import uvicorn
    import enum

    app = fastapi.FastAPI()
    M = TypeVar("M")

    class Event(BaseModel):
        id: int
        name: str

    # https://docs.pydantic.dev/latest/concepts/models/#generic-models
    class PaginatedResponse(BaseModel, Generic[M]):
        total_count: Optional[int] = Field(
            default=None,
            description="Total number of items in the database",
            examples=[300],
        )
        offset: int = Field(
            description="Starting position index of the returned items in the dataset.",
            examples=[299],
        )
        count: int = Field(
            description="Number of items returned in the response", examples=[1]
        )
        items: List[M] = Field(
            description="List of items returned in the response following given criteria"
        )

    class MetaQueryParamsChangeTypeHintsForOrderBy(type):

        def __new__(cls, name, bases, attr):
            if attr["__orig_bases__"][0] != Generic[M]:
                target_model_attributes = list(
                    get_args(attr["__orig_bases__"][0])[0].model_fields.keys()
                )
                print("name", name)
                print("attr", attr)
                print(
                    "bases",
                )
                new_typing_with_literals = Literal[tuple(target_model_attributes)]

                print("new_literals", new_typing_with_literals)
                print(
                    'bases[0].__init__.__annotations__["order_by"]',
                    bases[0].__init__.__annotations__["order_by"],
                )
                bases[0].__init__.__annotations__["order_by"] = new_typing_with_literals
                print(
                    "__init__",
                    list(inspect.signature(bases[0].__init__).parameters.items())[1:],
                )
                for index, init_param_name in enumerate(
                    list(inspect.signature(bases[0].__init__).parameters.keys())[1:]
                ):
                    print("index", index)
                    print("init_param_name", init_param_name)
                    print(
                        "bases[0].__init__.__defaults__[index]",
                        bases[0].__init__.__defaults__[index],
                    )
                    if init_param_name in attr["defaults"]:
                        print("SET ", init_param_name, "TO ", attr["defaults"])
                        new_defaults = list(bases[0].__init__.__defaults__)
                        new_defaults[index] = attr["defaults"][init_param_name]
                        bases[0].__init__.__defaults__ = tuple(new_defaults)

                print("default", bases[0].__init__.__defaults__)

            # print(attr["__init__"])
            return super().__new__(cls, name, bases, attr)

    class QueryParams(Generic[M], metaclass=MetaQueryParamsChangeTypeHintsForOrderBy):
        defaults = dict()

        def __init__(
            self,
            q: str | None = None,
            skip: Annotated[int, Field(description="This is text")] = 0,
            limit: int = 100,
            order_by: Optional[Literal["Generic", "Placeholder"]] = None,
        ):
            self.q = q
            self.skip = skip
            self.limit = limit
            self.order_by = order_by

    class EventQueryParams(QueryParams[Event]):
        defaults = {"limit": 50}

    @app.get(
        "/event2",
        response_model=PaginatedResponse[Event],
        description=f"List all events.",
    )
    def list_events2(
        query: Annotated[EventQueryParams, Depends(EventQueryParams)],
    ) -> PaginatedResponse[Event]:
        return PaginatedResponse(
            total_count=events,
            offset=query.offset,
            count=len(events),
            items=events[query.offset, query.limit],
        )

    uvicorn.run(
        app,
        host="localhost",
        port=8181,
    )


def GenericTyping():
    from typing import Generic, Literal, TypeVar, get_type_hints, get_args
    from pydantic import BaseModel

    M = TypeVar("M")

    class MetaQueryParamsChangeTypeHinterForOrderBy(type):

        def __new__(cls, name, bases, attr):
            if attr["__orig_bases__"][0] != Generic[M]:
                event_attributes = list(
                    get_args(attr["__orig_bases__"][0])[0].model_fields.keys()
                )
                print("name", name)
                print("attr", attr)
                print(
                    "bases",
                )
                bases[0].__init__.__annotations__["order_by"].__dict__["__args__"] = (
                    tuple(event_attributes)
                )

                print(
                    "Event keys",
                    list(get_args(attr["__orig_bases__"][0])[0].model_fields.keys()),
                )

            # print(attr["__init__"])
            return super().__new__(cls, name, bases, attr)

    class QueryParams(Generic[M], metaclass=MetaQueryParamsChangeTypeHinterForOrderBy):

        def __init__(
            self,
            items: List[M],
            order_by: Optional[Literal["Generics", "Placeholder"]] = None,
        ):
            # print(get_type_hints(self.__class__.__init__)["items"])
            actual_type = tuple(self.__orig_bases__[0].__args__[0].model_fields.keys())
            # print(actual_type)
            self.items = items
            self.order_by = order_by

    class Event(BaseModel):
        id: int
        name: str

    class EventQueryParams(QueryParams[Event]):
        pass

    q = EventQueryParams([Event(id=1, name="A"), Event(id=2, name="B")], "id")

    print(get_type_hints(EventQueryParams.__init__))


def get_enum_by_str():
    from enum import Enum

    class MyEnum(str, Enum):
        ONE = 1
        TWO = 2

    val = "ONE"
    print(MyEnum[val].value)


def get_class_attr_as_str():
    from pydantic import BaseModel

    class Event(BaseModel):
        order_index: str

    attr_name = Event.order_index
    print(type(attr_name), attr_name)
    # does not work :(


def nested_pydnatic_val():
    from pydantic import Field
    from pydantic_settings import BaseSettings

    class Config(BaseSettings):
        APP_NAME: str = "DZD CheckCheck"
        FRONTEND_FILES_DIR: str = Field(
            description="The generated nuxt dir that contains index.html,...",
            default="CheckCheck/frontend/.output/public",
        )
        FRONTED_FILE: str = Field(
            description="The generated nuxt dir that contains index.html,...",
            default=FRONTEND_FILES_DIR,
        )

    print(Config())


def path_test():
    from pathlib import Path

    print(Path(Path(__file__).parent, "default.yaml"))


def pydnatic_shadow_warning_test():
    # /usr/local/lib/python3.11/site-packages/pydantic/_internal/_fields.py:201: UserWarning: Field name "created_at" in "EventExport" shadows an attribute in parent "Event"
    from sqlmodel import SQLModel, Field

    # from pydantic import BaseModel, Field
    import datetime

    class Event(SQLModel, table=True):
        name: str = Field(primary_key=True)
        created_at: datetime.datetime = Field()

    class EventExport(Event, table=False):
        created_at: datetime.datetime = Field(exclude=True)


def get_attr_name():
    from pydantic import BaseModel

    class Test(BaseModel):
        password: str

    print(Test.password.__name__)


def one2one():
    import uuid
    from sqlmodel import SQLModel, Field, Relationship, ForeignKeyConstraint

    class CheckListItemPosition(SQLModel, table=True):
        __tablename__ = "checklist_item_pos"
        checklist_item_id: uuid.UUID = Field(
            foreign_key="checklist_item.id", primary_key=True
        )
        checklist_item: "CheckListItem" = Relationship(
            back_populates="position",
            sa_relationship_kwargs={
                "lazy": "selectin",
                "single_parent": True,
            },
        )

    class CheckListItem(SQLModel, table=True):
        __tablename__ = "checklist_item"
        id: int = Field(
            primary_key=True,
            index=True,
            nullable=False,
            unique=True,
            # sa_column_kwargs={"server_default": text("gen_random_uuid()")},
        )
        text: str = Field(description="The display name of the list")
        checklist_id: uuid.UUID = Field(foreign_key="checklist.id")
        position: CheckListItemPosition = Relationship(back_populates="checklist_item")


def start_nested_async():
    import asyncio

    async def main_async():
        child_nonasync()

    async def grand_child_async():
        print("CALLED!")
        await asyncio.sleep(1)
        print("Finished!")

    def child_nonasync():
        loop = asyncio.get_event_loop()
        loop.run_until_complete(grand_child_async())

    asyncio.get_event_loop().run_until_complete(main_async())


def deci():
    import decimal

    print(
        ((decimal.Decimal(str(2.0)) - decimal.Decimal(str(1.6))) / 2)
        + decimal.Decimal(str(1.6))
    )


def slugify_string(s: str) -> str:
    return "".join(
        char.lower() if char.isalnum() else "-"
        for char in s
        if char.isalnum() or char == " "
    )


def fernet_enc():
    from cryptography.fernet import Fernet
    import hashlib
    import base64
    from typing import Dict
    import json

    def generate_fernet_key(input_str: str) -> bytes:
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

    enrypction_key = "seeeecret"
    fernet = Fernet(generate_fernet_key(enrypction_key))

    def encrypt_token(token: Dict) -> str:
        return fernet.encrypt(json.dumps(token).encode()).decode()

    def decrypt_token(token_encrypted: str) -> dict:
        return json.loads(fernet.decrypt(token_encrypted).decode())

    secret_message_input = {"message": "Hello this is secret message"}
    print("secret_message_input", secret_message_input)
    encrypted_secret_message_input = encrypt_token(secret_message_input)

    print("encrypted_secret_message_input", encrypted_secret_message_input)
    print(
        "decrypted_secret_message_input", decrypt_token(encrypted_secret_message_input)
    )


def optional_min_length_in_pydantic():
    from typing import Optional
    from pydantic import BaseModel, Field

    class User(BaseModel):
        password: Optional[str] = Field(
            default=None,
            min_length=10,
            description="The password of the user. Can be None if user is authorized by external provider. e.g. OIDC",
        )

    user_with_no_pw = User.model_validate({"password": None})  # passes
    print("user_with_no_pw", user_with_no_pw)
    user_with_long_pw = User.model_validate(
        {"password": "werg423wrefdsr32wef"}
    )  # passes
    print("user_with_long_pw", user_with_long_pw)
    user_with_too_short = User.model_validate({"password": "werg"})  # fails
    print("user_with_too_short", user_with_too_short)


def ListObjviaEnv():
    import os

    os.environ["APP_NAME"] = "other"
    os.environ["LIST_VAL__0__VAL1"] = "1"
    os.environ["LIST_VAL__0__VAL2"] = "1"

    from typing import List
    from pydantic import BaseModel
    from pydantic_settings import BaseSettings, SettingsConfigDict

    class SubConfigObject(BaseModel):
        VAL1: str
        VAL2: str

    class Config(BaseSettings):
        APP_NAME: Optional[str] = "CheckCheck"
        LIST_VAL: Optional[List[SubConfigObject]] = None

        model_config = SettingsConfigDict(
            env_nested_delimiter="__",
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
        )

    c = Config()
    print(c.model_dump_json())
    # does not work with 2.6.1. there are some discussions around that here https://github.com/pydantic/pydantic-settings/issues/376
    # but it seems at the moment this will be possible in near future


ListObjviaEnv()
