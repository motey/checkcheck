"""Read side of the WI-4 global sync-sequence counter.

The write side (allocation + the ``sync_seq`` table itself) lives in
``model/_base_model.py`` so it can sit next to the mapper stamping events. This
module only exposes the current high-water mark, which the delta feed uses as the
``next_cursor`` it hands back to clients.
"""

from sqlalchemy import text
from sqlmodel.ext.asyncio.session import AsyncSession

from checkcheckserver.model._base_model import SYNC_SEQ_ROW_ID


async def get_current_server_seq(session: AsyncSession) -> int:
    """Highest ``server_seq`` handed out and committed so far.

    Read *before* the delta feed runs its entity queries so the returned
    ``next_cursor`` can never sit above a row the same pull failed to include:
    at worst a row committed after this read is delivered now but re-delivered on
    the next pull (harmless, LWW is idempotent), never skipped.
    """
    result = await session.execute(
        text("SELECT value FROM sync_seq WHERE id = :row_id"),
        {"row_id": SYNC_SEQ_ROW_ID},
    )
    return result.scalar_one()
