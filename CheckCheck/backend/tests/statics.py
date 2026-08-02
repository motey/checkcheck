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

# Sender address the test instance (and the mail_capture fixture) is configured
# with. Tests may assert on it.
MAIL_CAPTURE_FROM_ADDRESS = "checkcheck-tests@example.com"

# Declared as SHARING_INTERNAL_EMAIL_DOMAINS on the test instance (chunk E6), so
# the "that address looks like a colleague" hint has a real domain to fire on.
INTERNAL_TEST_EMAIL_DOMAIN = "internal-test.de"

# ── OIDC mock constants — consumed by tests_oidc_mapping.py and conftest.py ────
OIDC_TEST_PROVIDER_DISPLAY_NAME = "LocalTestOIDC"
OIDC_TEST_PROVIDER_SLUG = "localtestoidc"
# An OIDC group that ROLE_MAPPING maps to the CheckCheck "usermanager" role.
OIDC_TEST_ROLE_GROUP = "oidc-group-managers"
OIDC_TEST_MAPPED_ROLE = "usermanager"
