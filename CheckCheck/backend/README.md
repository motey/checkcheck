# DZDCheckCheck Server

The server module for DZDCheckCheck, a system to document the medical history of study participant.

## Local Setup

###  Install req only


`python -m pip install pip-tools -U`

`python -m piptools compile -o CheckCheck/backend/requirements.txt CheckCheck/backend/pyproject.toml`

`python -m pip install -r CheckCheck/backend/requirements.txt -U`


### FAQ

* The return to the /auth ednpoint from the Authentik OIDC provider fails with an "ValueError: Invalid key set format" error
  * I had to update the provider in authentik. seems to be an issue with the self-signed singing key in the demo env

## Run

### Run demo with pre build docker image (recomended)

`docker run -e DEMO_MODE=true motey/checkcheck-server`

### Run backend worker seperate

Start server without background worker
`docker run -v ./data:/data -e DEMO_MODE=true -e BACKGROUND_WORKER_IN_EXTRA_PROCESS=false motey/checkcheck-server`


Start extra container with background worker
`docker run -v ./data:/data -e BACKGROUND_WORKER_IN_EXTRA_PROCESS=false motey/checkcheck-server --run_worker_only`

### Alembic - Database Migrations

`alembic init -t async CheckCheck/backend/migrations`

add to script.py.mako

```
import sqlmodel
```

add to env.py

```
# Tim: Pull sqlurl from checkcheckserver config
import sys
import os
from pathlib import Path

MODULE_DIR = Path(__file__).parent
MODULE_PARENT_DIR = MODULE_DIR.parent.absolute()
sys.path.insert(0, os.path.normpath(MODULE_PARENT_DIR))
from checkcheckserver.config import Config as CheckCheckServerConfig

checkcheckserver_config = CheckCheckServerConfig()
config.set_main_option("sqlalchemy.url", str(checkcheckserver_config.SQL_DATABASE_URL))


# Tim: Import CheckCheckServer Models
from sqlmodel import SQLModel
from alembic import context
from checkcheckserver.model._tables import *

target_metadata = SQLModel.metadata
``` 

#### Change the data(-base) model

When making a change on a `=table`-class (Or a parent class) in `CheckCheck/backend/checkcheckserver/model` you'll need to tell alembic to create a migration script.  
This way existing instances can upgrade their database when doing updates.

In this repos rootfolder run

`alembic revision --autogenerate -m "<A short message what did changed>"`