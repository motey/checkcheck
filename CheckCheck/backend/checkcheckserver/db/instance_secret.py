"""Reading and writing the instance's own generated secrets (chunk K1).

Plain functions, like ``db/push_subscription.py``: the only thing anybody needs
is "give me this secret, generating it once if it does not exist yet".
"""

from typing import Callable, Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.log import get_logger
from checkcheckserver.model.instance_secret import InstanceSecret


log = get_logger()


async def get(session: AsyncSession, name: str) -> Optional[str]:
    """The stored value for *name*, or None when this instance has none."""
    row = (
        await session.exec(select(InstanceSecret).where(InstanceSecret.name == name))
    ).one_or_none()
    return row.value if row is not None else None


async def get_or_create(
    session: AsyncSession, name: str, factory: Callable[[], str]
) -> str:
    """The stored value for *name*, generating and storing one if there is none.

    *factory* is only called when the row is missing, and its result is only kept
    if this caller wins the INSERT. That last part is the whole point: two
    replicas booting at the same second both find nothing, both generate their
    own perfectly valid secret, and both try to insert it. The primary key lets
    exactly one of them through, and the loser **re-reads the winner's row
    rather than overwriting it** (the same pattern as
    ``db/push_subscription.upsert``). Overwriting would leave the two replicas
    signing with different keys, which is precisely the split-brain this table
    exists to prevent.
    """
    existing = await get(session, name)
    if existing is not None:
        return existing

    row = InstanceSecret(name=name, value=factory())
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        winner = await get(session, name)
        if winner is None:
            # Not the race, then: something else rejected the insert and there is
            # nothing to fall back on.
            raise
        log.debug(
            "[instance-secret] lost the insert race for %r, using the stored value", name
        )
        return winner
    await session.refresh(row)
    return row.value
