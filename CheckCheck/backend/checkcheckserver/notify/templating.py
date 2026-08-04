"""Jinja2 rendering for outgoing email and the unsubscribe page (chunk E7).

Two loaders are really one: an operator's ``EMAIL_TEMPLATE_DIR`` sits in front
of the bundled directory in a :class:`jinja2.ChoiceLoader`, so overriding a
single template name (``base.html`` alone is enough to rebrand everything) is
resolved every time any template does ``{% extends %}`` or ``{% include %}``,
not only for the top-level name a caller asks for. Anything the operator does
not provide falls straight through to the bundled version, unchanged.

``autoescape`` is on for ``.html`` and off for ``.txt`` (:func:`select_autoescape`
picks it per extension), and ``undefined=StrictUndefined`` turns a renamed or
missing context key into a hard failure rather than a silently blank sentence.
That matters twice over here: it is what makes the test suite notice a drifted
template, and it is the mechanism that keeps ``NOTIFY_EMAIL_CONTENT_MODE:
minimal`` honest, since the context builders in ``render.py`` and
``invitation.py`` simply omit a key like ``card_name`` in that mode rather than
setting it to ``None`` -- a bundled template never references those optional
keys, but an operator override that does gets a hard failure instead of a
silent leak.

Two different failure moments matter, and this module treats them differently:

* A template that fails to **compile** (a syntax error) is meant to be caught
  once, at startup: :func:`check_all_templates`, called from ``Config``'s own
  boot-time validation, renders every bundled template name against a dummy
  context through the *active* environment (override included), so a broken
  override fails loudly before the first real message is ever queued.
* A template that raises while **rendering** a real message (including one
  that only fails on data the dummy context did not happen to exercise) is
  caught by :func:`render`, logged, and re-rendered against the bundled-only
  environment, because a typo in an operator's footer must not stop a reminder
  from arriving.
"""

from __future__ import annotations

import functools
import logging
from pathlib import Path
from typing import Optional

import jinja2

from checkcheckserver.config import Config


# Not checkcheckserver.log.get_logger(): that module constructs its own
# module-level Config(), and this module is imported from inside a Config
# model validator (check_all_templates, called from _validate_and_check_
# email_branding). Reaching back into checkcheckserver.log from here would
# recurse into a second, nested Config() construction while the first one is
# still mid-validation. Plain stdlib logging resolves to the same named
# logger once checkcheckserver.log configures it elsewhere.
log = logging.getLogger("CheckCheck")

BUNDLED_TEMPLATE_DIR = Path(__file__).parent / "templates"

# Every template name an operator may override. Kept here (rather than
# scattered across render.py/invitation.py) so check_all_templates() has one
# place to enumerate.
ALL_TEMPLATE_NAMES = (
    "message.html",
    "message.txt",
    "invitation.html",
    "invitation.txt",
    "test_email.html",
    "test_email.txt",
    "unsubscribe_page.html",
)

# Config() runs this at every construction (it is one of the model's own
# boot-time validators), and plenty of modules build a throwaway Config() at
# import time. Once a given EMAIL_TEMPLATE_DIR value has passed the check
# there is nothing left to learn by repeating it, so cache success by that one
# value. A failure is never cached, deliberately: cached would suppress the
# ValueError the next Config() attempt needs to raise, right when a test
# constructing one with a fixed-up override directory expects it to succeed.
_CHECKED_OVERRIDE_DIRS: set = set()


@functools.lru_cache(maxsize=8)
def _environment(override_dir: Optional[str]) -> jinja2.Environment:
    loaders = []
    if override_dir:
        loaders.append(jinja2.FileSystemLoader(override_dir))
    loaders.append(jinja2.FileSystemLoader(str(BUNDLED_TEMPLATE_DIR)))
    return jinja2.Environment(
        loader=jinja2.ChoiceLoader(loaders),
        autoescape=jinja2.select_autoescape(
            enabled_extensions=("html",), default_for_string=False
        ),
        undefined=jinja2.StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _active_environment(config: Config) -> jinja2.Environment:
    return _environment(config.EMAIL_TEMPLATE_DIR)


def _bundled_environment() -> jinja2.Environment:
    return _environment(None)


def render(name: str, context: dict, *, config: Config) -> str:
    """Render *name* against *context*, the override directory taking priority.

    Falls back to the bundled template, logging the error, when an override
    compiles fine but raises while rendering *this* context. A template that
    fails to compile at all is a startup-time failure (see
    :func:`check_all_templates`) and is left to raise here too, since reaching
    this function at all means it already passed that check.
    """
    try:
        return _active_environment(config).get_template(name).render(**context)
    except Exception:
        if not config.EMAIL_TEMPLATE_DIR:
            raise
        log.error(
            "[notify] template override for %r failed to render, falling back to "
            "the bundled template",
            name,
            exc_info=True,
        )
        return _bundled_environment().get_template(name).render(**context)


def check_all_templates(config: Config) -> None:
    """Render every bundled template name against a dummy context.

    Called from ``Config``'s own boot-time validation. A syntactically broken
    override (or a code bug in a bundled template) fails loudly here, before
    the instance starts, rather than dead-lettering mail the first time
    somebody's card gets shared.
    """
    override_dir = config.EMAIL_TEMPLATE_DIR
    if override_dir in _CHECKED_OVERRIDE_DIRS:
        return

    # Imported here, not at module load: boot_templates builds its dummy data
    # from branding.brand_context(), and this keeps that dependency one-way.
    from checkcheckserver.notify import boot_templates

    env = _active_environment(config)
    for name in ALL_TEMPLATE_NAMES:
        context = boot_templates.dummy_context(name, config)
        try:
            env.get_template(name).render(**context)
        except Exception as exc:
            raise ValueError(
                f"Email template {name!r} failed to render at startup: {exc}"
            ) from exc

    _CHECKED_OVERRIDE_DIRS.add(override_dir)
