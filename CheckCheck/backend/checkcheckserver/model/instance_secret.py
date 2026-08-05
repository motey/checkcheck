"""Secrets this instance generated for itself (chunk K1 of
``docs/plans/PUSH_KEYS_AND_SHARE_SETTING.md``).

One row per named secret. The first, and so far only, tenant is the VAPID key
pair the push channel signs with: an instance that has no ``VAPID_PUBLIC_KEY`` /
``VAPID_PRIVATE_KEY`` configured generates one on first boot and keeps it here
(``notify/vapid.py``).

**Why the database and not a file.** A generated key has to survive a restart or
every subscribed device is silently orphaned, and it has to be the *same* key on
every replica or half the pushes are signed with a key the browser never
subscribed to. The container filesystem is neither: it may be read-only, it is
often ephemeral, and two replicas writing their own file each end up with their
own pair, which is a broken instance that looks perfectly healthy. The database
is the one thing every replica already shares and the deployment already backs
up. See decision 2 of the plan.

**Not a configuration system.** It holds values the server generated for itself,
never operator settings: anything an operator is meant to choose belongs in
``config.py``, where it is documented, validated at boot and visible in
``docs/config``. The table is general only so the next generated secret does not
need its own table, and ``name`` is the whole of its schema on purpose.

Like the outbox and the push-subscription table this is delivery plumbing, not a
syncable entity: no client ever sees it through the delta feed.
"""

from sqlmodel import Field

from checkcheckserver.model._base_model import TimestampedModel


# Long enough for any base64url-encoded key material this will hold, short enough
# that the column stays a plain varchar on both backends.
INSTANCE_SECRET_VALUE_MAX_LENGTH = 2048


class InstanceSecret(TimestampedModel, table=True):
    __tablename__ = "instance_secret"

    name: str = Field(
        primary_key=True,
        max_length=64,
        description=(
            "What this secret is, e.g. `vapid_private_key`. The primary key, which "
            "is what makes 'generate it once' a database constraint rather than a "
            "convention: two replicas booting at the same second race on the "
            "INSERT and exactly one of them wins."
        ),
    )
    value: str = Field(
        max_length=INSTANCE_SECRET_VALUE_MAX_LENGTH,
        description="The secret itself, as the generator produced it (base64url for key material).",
    )
