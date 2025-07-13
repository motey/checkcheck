#!/bin/bash
#Script to generate and oldschool requierments.txt from pyproject.toml

# old non pdm version
#python -m pip install pip-tools -U
#rm requirements.txt
#python -m piptools compile -o requirements.txt pyproject.toml

# New PDM version
rm -f CheckCheck/backend/requirements.txt
rm -f CheckCheck/backend/requirements_dev.txt
rm -f CheckCheck/backend/requirements_test.txt
(cd CheckCheck/backend && pdm lock && pdm export --without dev,test -o requirements.txt --without-hashes)
(cd CheckCheck/backend && pdm lock && pdm export --group dev -o requirements_dev.txt --without-hashes)
(cd CheckCheck/backend && pdm lock && pdm export --group test -o requirements_test.txt --without-hashes)