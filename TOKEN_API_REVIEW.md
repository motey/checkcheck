# API token (API key) review

Review of the self-service API key feature in CheckCheck, done on 2026-09-16 while building the
same feature in DZDMedLog (issue #198, branch `feature/issue-198-api-token-management`). Both code
bases grew out of the same auth backend, so most findings map one to one. Each finding names the
place in CheckCheck and, where there is one, the MedLog code that solves it.

Nothing in this repository was changed. Paths are relative to `CheckCheck/backend/checkcheckserver/`
unless noted. MedLog paths are relative to `DZDMedLog/MedLog/backend/medlogserver/`.

## Summary

| # | Severity | Finding |
|---|---|---|
| 1 | High | API keys bypass `RESTRICT_USER_SEARCH_TO_OWN_GROUPS` |
| 2 | High | An API key can create, list and delete API keys |
| 3 | High | Plain tokens and OIDC tokens are written to the log at DEBUG |
| 4 | Medium | Keys of OIDC users keep their groups and roles after the user left the provider |
| 5 | Medium | Token whose source login is gone gives a 500 instead of a 401 |
| 6 | Low | No feature switch, never-expiring keys allowed by default |
| 7 | Low | No limit on keys per user |
| 8 | Low | `last_used_at` is written on every request |
| 9 | Low | No unique index on `user_auth.api_token_id` |
| 10 | Info | No fallback for API token hashes written before the SHA-256 switch |
| 11 | Info | `include_revoked` has no effect |

What CheckCheck already does better than MedLog did, and MedLog has now adopted: SHA-256 token
hashing, `last_used_at`, secret check before deleting a token on logout, an upper bound for
`expires_in_days`, `201 Created`, admin endpoints to list and revoke a user's keys.

## Findings

### 1. API keys bypass the group restriction (High)

`caller_restricted_to_own_groups` (`api/auth/security.py:59`) only restricts a caller whose
`current_user_auth.auth_source_type` is `oidc`. For a self-created API key,
`get_current_user_auth` returns the key row itself (`api_token_source_user_auth_id` is `None`,
`api/auth/security.py:132-136`), so the type is `api_token` and the function returns `False`.

Effect: a user from a provider with `RESTRICT_USER_SEARCH_TO_OWN_GROUPS` creates a key in the UI,
then calls `GET /api/user/search` (`api/routes/routes_user.py:102`) or shares a checklist with any
group (`api/routes/routes_checklist_share.py:469`) with that key, without the group restriction.

Tokens from the OIDC token login are not affected: they resolve to their OIDC source login.

Fix: decide by the user, not by the credential. The user has `oidc_groups` and, if needed, the
provider can be stored on the user at login. Alternatively resolve a managed key to the user's
latest OIDC login before the check. Add a test that searches with a key of a restricted user.

### 2. An API key can manage API keys (High)

`/user/me/api-keys` (list, create, delete, `api/routes/routes_user.py:219-294`) use
`Security(get_current_user)`, which accepts API keys. A leaked key can mint new keys that outlive
its own expiry and revocation, so revoking the leaked key does not end the incident.

MedLog: `get_current_user_by_session` in `api/auth/security.py` answers 403 when an
`Authorization: Bearer` header is present and otherwise behaves like `get_current_user`. All three
token management endpoints use it. Test: `test_api_tokens_can_not_manage_api_tokens` in
`tests/tests_api_token_management.py`.

### 3. Secrets in DEBUG logs (High)

- `model/user_auth.py:233` `log.debug(f"TOKEN {token}")` logs the complete API key on every
  request made with a key.
- `api/routes/routes_auth.py:376` `log.debug(f"Token {token}")` logs the complete OIDC token
  response, including access, refresh and id token.
- `api/auth/utils.py:214` logs the OIDC token dict again.
- `UserAuthCreate.generate_api_token` assigns a plain `str` to the `SecretStr` field, so any repr
  of the create object (log line, traceback, debugger) shows the key.

Fix: remove the three log lines, and wrap the generated token in `SecretStr` (use
`get_secret_value()` in `get_api_token`). If any instance ever ran with `LOG_LEVEL=DEBUG`, treat
keys and refresh tokens in those logs as leaked.

MedLog removed the same lines and added `test_token_secrets_do_not_end_up_in_debug_logs` (collects
the output of all app loggers at DEBUG during token validation) and
`test_plain_token_is_hidden_in_the_create_object_repr`.

### 4. Keys of OIDC users outlive the user's access at the provider (Medium)

`oidc_groups` and roles are only synced at OIDC login. A self-created key is bound to the user, not
to a login, so a user removed from the provider (or from a group) keeps the old groups and roles
through the key until it expires. With `API_TOKEN_ALLOW_NEVER_EXPIRE=True` (the default) that can
be forever. Only deactivating the user in CheckCheck stops it.

MedLog solution:

- `User.last_oidc_login_at`, set in the OIDC callback after the group mapping.
- `API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS` (default 30, `None` disables).
- `get_current_user` rejects a managed key with a 401 and a readable message when the user has an
  OIDC login time older than that. The key is paused, not deleted: the next browser login
  reactivates it. Users who never logged in via OIDC are not affected.

### 5. Missing source login gives a 500 (Medium)

`api/auth/security.py:133-136`: when a token's `api_token_source_user_auth_id` points to a login
that no longer exists (logout, cleanup), `user_auth` becomes `None` and line 150
`user_auth.is_expired()` raises `AttributeError`. MedLog raises `not_authenticated_exception`
there.

### 6. No feature switch, never-expiring keys by default (Low)

Keys are always available and `API_TOKEN_ALLOW_NEVER_EXPIRE` defaults to `True`. The lifetime of
keys and of tokens from the token login share `API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES`.

MedLog uses separate settings: `API_TOKEN_MANAGEMENT_ENABLED` (default off; switching it off also
rejects existing managed keys), `API_TOKEN_MANAGEMENT_DEFAULT_EXPIRY_DAYS` (30) and
`API_TOKEN_MANAGEMENT_MAX_EXPIRY_DAYS` (365, capped at 3650), with a validator that the default
does not exceed the maximum. `GET /api/config/api-token` gives the web client all of these values.

### 7. No limit on keys per user (Low)

Any user can create unlimited keys. MedLog: `API_TOKEN_MANAGEMENT_MAX_TOKENS_PER_USER` (default 20,
unexpired managed keys only), `409 Conflict` beyond it.

### 8. `last_used_at` written on every request (Low)

`db/user_auth.py:201` `touch_last_used_at` commits on every authenticated key request, so a busy
script turns every read into a write. MedLog's `record_api_token_use` writes at most once per
minute (`API_TOKEN_LAST_USED_WRITE_INTERVAL`) and uses the same write to upgrade legacy hashes
(see 10).

### 9. No unique index on `api_token_id` (Low)

Every key request looks the key up by `api_token_id` with `one_or_none()`, without an index.
Collisions are practically impossible (96 bit), but the lookup is a table scan and a duplicate
would be a 500. MedLog added a unique index `ix_user_auth_api_token_id` (NULL is allowed multiple
times on Postgres and SQLite).

### 10. No fallback for old token hashes (Info)

`model/_hashing.py` hashes keys with SHA-256. I found no code that still verifies hashes written
before that switch. If keys or login tokens with an older hash existed on a running instance, they
stopped working. Confirm whether that mattered. MedLog detects the old PBKDF2 hash by its length,
still verifies it, and rehashes to SHA-256 on the next successful use, the only moment the plain
token is known.

### 11. `include_revoked` has no effect (Info)

Both delete endpoints hard-delete the row, nothing sets `revoked=True`, so `include_revoked` on the
list endpoints never returns more. Either drop the parameter or switch to soft revocation.

## Also worth checking

- Logout with a key (`api/routes/routes_auth.py:485`) verifies the secret before deleting. MedLog
  deleted by id prefix alone; fixed there now.
- `admin_list_user_api_keys` uses `user_crud.get` without `show_deactivated=True`, so an admin gets
  a 404 for the keys of a deactivated user.
- Token management needs no password re-entry (neither project). The session cookie is `httponly`
  and `SameSite=Lax`, which limits the risk to a stolen session.

## Assessment (2026-09-18)

All findings were checked against the code. Findings 1 to 9 and 11 are confirmed. Additions and
corrections:

- Finding 3 is wider than listed. Also leaking at DEBUG:
  - `api/auth/utils.py:103` `log.debug(f"refresh_token: {refresh_token}")` logs the raw OIDC
    refresh token on every refresh.
  - `api/routes/routes_auth.py:89` `log.debug(f"request.headers: {request.headers}")` logs all
    request headers on `/auth/list`, including the session cookie and any `Authorization` header.
  - `api/auth/utils.py:255` (`raw_userinfo`, personal data) and `api/routes/routes_auth.py:510`
    (`current_user_auth` repr) are noise that should go as well.
- Finding 10 does not apply: the SHA-256 switch landed on 2026-06-22 (`3103cd2`), before the first
  tag `0.0.3-beta.1` (2026-07-12). No released instance ever stored an old hash. No action.
- Finding 6: do not ship `API_TOKEN_MANAGEMENT_ENABLED` defaulting to off. Existing deployments
  would silently lose working keys on upgrade. Keep the feature on by default and make the
  lifetime defaults stricter instead.
- Password re-entry for token management: skipped for now, only worth considering after chunk 1.

## Work chunks

Each chunk is sized for one session. Chunks 1 and 2 close the High findings and come first.
Chunks 3 and 4 can wait. Chunk 4 can be folded into chunk 2 to ship a single migration release.

### Chunk 1: Stop the leaks (findings 3, 2, 5)

Backend only, no migration.

- [x] Remove the secret log lines: `model/user_auth.py:233`, `api/routes/routes_auth.py:376`,
      `api/auth/utils.py:214`, `api/auth/utils.py:103`, `api/routes/routes_auth.py:89`.
      Also drop `api/auth/utils.py:255` and `api/routes/routes_auth.py:510`.
- [x] `UserAuthCreate.generate_api_token`: store the token as `SecretStr`;
      `get_api_token` uses `get_secret_value()`.
- [x] Add `get_current_user_by_session` in `api/auth/security.py`: 403 when an
      `Authorization: Bearer` header is present, otherwise same as `get_current_user`.
      Use it on list, create and delete of `/user/me/api-keys`.
- [x] `get_current_user_auth`: raise `not_authenticated_exception` when the token's source login
      (`api_token_source_user_auth_id`) no longer exists, instead of crashing with a 500.
- [x] Tests (port from MedLog `tests/tests_api_token_management.py`): secrets absent from all app
      loggers at DEBUG during token validation and OIDC refresh; plain token hidden in the create
      object repr; an API key gets 403 on all three key management endpoints; token with a deleted
      source login gets 401.
- [x] Release note: instances that ever ran with `LOG_LEVEL=DEBUG` should treat API keys and OIDC
      refresh tokens in those logs as leaked (rotate keys, log out sessions).
- Done 2026-09-18. Also removed a log line the review missed:
  `api/routes/routes_user_management.py:193` logged the plain password when an admin set a
  user's first password. Tests: `tests/tests_api_token_security.py` (unit, fake CRUDs) and
  `test_api_tokens_can_not_manage_api_keys` in `tests/tests_auth.py`. The key management tests
  in `tests_auth.py` now use a browser session. Release notes in `CHANGELOG.md` (Security) and
  `docs/UPGRADING.md`.

### Chunk 2: Identity-based OIDC checks (findings 1, 4)

Backend, one Alembic migration.

- [x] Add `User.oidc_provider_slug` and `User.last_oidc_login_at` (naive UTC), set in the OIDC
      callback after the group mapping.
- [x] `caller_restricted_to_own_groups` decides by the user (provider stored on the user), not by
      the credential type. Callers: user search (`api/routes/routes_user.py`) and group share
      (`api/routes/routes_checklist_share.py`).
- [x] New setting `API_TOKEN_MANAGEMENT_OIDC_LOGIN_MAX_AGE_DAYS` (default 30, `None` disables).
      `get_current_user` rejects a managed key with a 401 and a readable message when the user's
      last OIDC login is older. The key is paused, not deleted; the next browser login
      reactivates it. Users without an OIDC login time are not affected.
- [x] Tests: user search and group share with a key of a restricted user are restricted; key of a
      user with a stale OIDC login gets 401 and works again after a fresh login; local users
      unaffected.
- [x] Deployment note: `last_oidc_login_at` starts as NULL, so existing OIDC users are exempt
      until their next OIDC login. The group restriction for keys only applies once
      `oidc_provider_slug` is populated (next login), unless the migration backfills it from the
      user's latest OIDC `user_auth` row (preferred, check during implementation).
- Done 2026-09-18. Migration `0019_user_oidc_login.py` backfills `oidc_provider_slug` from the
  user's newest OIDC `user_auth` row and leaves `last_oidc_login_at` NULL (no key pauses on
  upgrade). The fields live on `User` only (plus the internal `UserUpdateOidcLogin`), not on
  the admin update body, and show up read-only in `/api/user/me` and the admin user views.
  `reject_paused_api_key` runs in `get_current_user` and in the SSE principal resolver; logout
  with a paused key still works. Only managed keys (type `api_token` after resolving) pause;
  login tokens are checked against the provider through their source login. The login time is
  set on OIDC login only, not on token refresh. Remaining gap: an OIDC user without any stored
  OIDC login at upgrade time stays unrestricted through a key until their next OIDC login.
  Tests: `tests/tests_api_token_oidc.py` (HTTP) and the pause rule in
  `tests/tests_api_token_security.py`.

### Chunk 3: Key policy settings (findings 6, 7)

Backend and frontend.

- [ ] Separate settings for managed keys, independent of the login token lifetime
      (`API_TOKEN_DEFAULT_EXPIRY_TIME_MINUTES` stays for login tokens):
      `API_TOKEN_MANAGEMENT_DEFAULT_EXPIRY_DAYS` (30 or 90),
      `API_TOKEN_MANAGEMENT_MAX_EXPIRY_DAYS` (365, capped at 3650, validator: default <= max),
      never-expiring keys off by default, `API_TOKEN_MANAGEMENT_MAX_TOKENS_PER_USER` (20,
      unexpired managed keys only, 409 beyond it). Existing keys keep working.
- [ ] `GET /api/config/api-token` exposes these values.
- [ ] Frontend key manager uses them for the default lifetime, the maximum and the "Never" option,
      and shows the 409 message.
- [ ] Regenerate `openapi.json` and frontend types; extend `frontend/tests/e2e/api-keys.spec.ts`;
      backend tests for limit and expiry bounds.
- [ ] Document the new settings and the changed defaults in the release notes.

### Chunk 4: Housekeeping (findings 8, 9, 11, admin 404)

Backend, one Alembic migration (or fold into chunk 2).

- [ ] `touch_last_used_at`: write at most once per minute per key.
- [ ] Unique index `ix_user_auth_api_token_id` on `user_auth.api_token_id`. The migration first
      checks for duplicate values and fails with a clear message if any exist.
- [ ] Remove `include_revoked` from `GET /user/me/api-keys` and the admin list endpoint (nothing
      sets `revoked`, the frontend does not use it). Regenerate `openapi.json`.
- [ ] `admin_list_user_api_keys`: `user_crud.get(..., show_deactivated=True)` so admins can see
      keys of deactivated users.
- [ ] Tests for the throttled write and the admin listing of a deactivated user.
