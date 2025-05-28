#FRONTEND BUILD STAGE


FROM oven/bun AS medlog-frontend-build
RUN mkdir /frontend_build
WORKDIR /frontend_build
COPY CheckCheck/frontend /frontend_build
RUN bun install && bun run build && bunx nuxi generate

# BACKEND BUILD AND RUN STAGE
FROM python:3.11 AS medlog-backend
RUN python3 -m pip install --upgrade pip
COPY --from=medlog-frontend-build /frontend_build/.output/public /app 
ENV DOCKER_MODE=1
ENV FRONTEND_FILES_DIR=/app
#RUN apt-get update && apt-get install git -y
#
ARG APPNAME=DZDCheckCheck
ARG MODULENAME=checkcheckserver
#
# prep stuff
RUN mkdir -p /opt/$APPNAME/$MODULENAME
WORKDIR /opt/$APPNAME
RUN pip install -U pip-tools

# Generate requirements.txt based on depenencies defined in pyproject.toml
COPY CheckCheck/backend/pyproject.toml /opt/$APPNAME/$MODULENAME
RUN pip-compile -o /opt/$APPNAME/requirements.txt /opt/$APPNAME/$MODULENAME/pyproject.toml

# Install requirements
RUN pip install -U -r /opt/$APPNAME/requirements.txt

# install app
COPY CheckCheck/backend/checkcheckserver /opt/$APPNAME/$MODULENAME

# copy .git folder to be able to generate version file
COPY .git /opt/$APPNAME/.git
RUN echo "__version__ = '$(python -m setuptools_scm 2>/dev/null | tail -n 1)'" > /opt/$APPNAME/$MODULENAME/__version__.py
# Remove git folder to reduce image size
RUN rm -r /opt/$APPNAME/.git

#Copy default app data provisioning files
RUN mkdir /prov
COPY CheckCheck/backend/provisioning_data /provisioning


# Install data
RUN mkdir -p /data/db
# set base config
WORKDIR /opt/$APPNAME/$MODULENAME
# set base config
ENV SERVER_LISTENING_HOST=0.0.0.0
ENV APP_PROVISIONING_DATA_YAML_FILES='[]'
ENV DRUG_TABLE_PROVISIONING_SOURCE_DIR=/data/provisioning/arzneimittelindex
ENV SQL_DATABASE_URL=sqlite+aiosqlite:////data/db/local.sqlite
ENTRYPOINT ["python", "./main.py"]
#CMD [ "python", "./main.py" ]