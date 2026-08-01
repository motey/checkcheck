"""Outgoing notification delivery (email today, webhooks later).

The in-app notification feed lives in ``db/notification.py``; this package is
about getting a notification *out* of the instance. Chunk E1 only contains the
transports (how a message physically leaves the server) plus the message
building; the outbox table, the dispatcher loop and the preference resolver
follow in later chunks. See ``docs/plans/EMAIL_NOTIFICATIONS.md``.
"""
