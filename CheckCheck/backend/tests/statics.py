from pathlib import Path

DB_PATH = f"{Path(__file__).parent}/testdb.sqlite"
DOT_ENV_FILE_PATH = f"{Path(__file__).parent}/.env"

ADMIN_USER_NAME = "admin3"
ADMIN_USER_PW = "password123"
ADMIN_USER_EMAIL = "admin@test.de"

# Pre-provisioned test user (defined in provisioning_data/test_users.yaml)
TEST_USER_NAME = "testuser01"
TEST_USER_PW = "testuserpw_secure1"
TEST_USER_EMAIL = "testuser01@test.de"
