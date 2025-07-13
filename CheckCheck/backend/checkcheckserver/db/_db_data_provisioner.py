from typing import List, Dict, Type, Callable, Optional, Tuple
import importlib
from sqlalchemy.ext.asyncio import AsyncConnection
from pathlib import Path, PurePath
from dataclasses import dataclass
import yaml

# internal imports


from checkcheckserver.config import Config
from checkcheckserver.log import get_logger
from checkcheckserver.utils import to_path
from checkcheckserver.db._session import get_async_session_context
from checkcheckserver.model._base_model import BaseTable
from checkcheckserver.db._base_crud import CRUDBase
from checkcheckserver.db.checklist import CheckListCRUD
from checkcheckserver.db.checklist_collaborator import CheckListCollaboratorCRUD
from checkcheckserver.db.checklist_item import CheckListItemCRUD
from checkcheckserver.db.checklist_position import CheckListPositionCRUD
from checkcheckserver.db.checklist_color_scheme import ChecklistColorSchemeCRUD
from checkcheckserver.db.user import UserCRUD
from checkcheckserver.db.user_auth import UserAuthCRUD

log = get_logger()
config = Config()
CRUD_classes: List[CRUDBase] = [
    UserCRUD,
    UserAuthCRUD,
    CheckListCRUD,
    CheckListCollaboratorCRUD,
    CheckListItemCRUD,
    CheckListPositionCRUD,
    ChecklistColorSchemeCRUD,
]


class DataProvisioner:
    def __init__(self, data_file: str | Path):
        data_file: Path = to_path(data_file)
        if not data_file.is_file():
            raise ValueError(
                f"Can not provision data from '{data_file}'. Error: Not a file path. Maybe you need to check config variable APP_PROVISIONING_DATA_YAML_FILES"
            )
        self.data_file = to_path(data_file)

    async def run(self):
        await self._parse_provisioning_file(self.data_file)

    async def _parse_provisioning_file(self, path: Path):
        file_content: Dict = None
        with open(path) as file_obj:
            try:
                file_content = yaml.safe_load(file_obj)
            except:
                log.error(
                    "Failed parsing provisioning data file at '{path}'. Data provisioning will be canceled."
                )
                raise
        if not "items" in file_content:
            raise ValueError(
                f"Unexpected format in provisioning data file at '{path}'. Data provisioning will be canceled."
            )
        if "items" in file_content and file_content["items"] is None:
            return
        for data_item in file_content["items"]:
            for class_path, class_data in data_item.items():
                print("Load:", class_path)
                crud_class, model_class = await self._get_crud_class_and_model_class(
                    class_path
                )
                await self._load_provsioning_data_item(
                    model_class, crud_class, class_data, file_path=path
                )

    async def _load_provsioning_data_item(
        self,
        model_cls: Type[BaseTable],
        crud_cls: Type[CRUDBase],
        class_data: List[Dict],
        file_path: Path,
    ):
        log.debug(
            f"Try inserting DB provisionig data for table '{model_cls.__tablename__}' ({model_cls}). Row count: {len(class_data)}"
        )
        for item in class_data:
            try:
                item_instance = model_cls.model_validate(item)
            except Exception as e:
                raise ValueError(
                    f"Could not validate provisioning for table '{model_cls.__tablename__}' from file '{file_path}'. Item Data:\n{item}\n Error:\n {e}"
                )
            async with get_async_session_context() as session:
                async with crud_cls.crud_context(session) as crud:
                    crud: CRUDBase = crud
                    try:
                        await crud.create(item_instance, exists_ok=True)
                    except:
                        log.error(
                            f"Could not provision database from file '{file_path.resolve()}' into table '{model_cls.__tablename__}'. ModelClass: {model_cls}. Provision object:\n{item_instance} "
                        )
                        raise

    async def _get_crud_class_and_model_class(
        self,
        class_path: str,
    ) -> Tuple[CRUDBase, BaseTable]:
        module_path, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        class_obj = getattr(module, class_name)

        for crudcls in CRUD_classes:
            if class_obj == crudcls.get_create_cls():
                return crudcls, class_obj
        raise ValueError(f"Expected '<class {class_path}>'. did not found in {module}")


async def provision_data(conn: AsyncConnection = None):
    log.info("Loading default data...")
    import __main__

    root_path = Path(__main__.__file__).parent
    default_data_yaml_path = Path(
        PurePath(root_path, Path(config.APP_PROVISIONING_DEFAULT_DATA_YAML_FILE))
    )
    print("default_data_yaml_path", default_data_yaml_path)
    data_provisioner = DataProvisioner(default_data_yaml_path)
    await data_provisioner.run()
    log.info("Try loading base data if configured...")
    for data_source_file in config.APP_PROVISIONING_DATA_YAML_FILES:
        data_provisioner = DataProvisioner(data_source_file)
        await data_provisioner.run()
    return None
