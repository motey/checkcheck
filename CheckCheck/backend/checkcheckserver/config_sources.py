"""List-aware layering of the config sources.

pydantic-settings splits a nested environment variable such as
``AUTH_OIDC_PROVIDERS__0__CLIENT_SECRET`` on ``env_nested_delimiter`` into
``{"AUTH_OIDC_PROVIDERS": {"0": {"client_secret": ...}}}``, but it never turns
the index keys back into a list, so a ``List[...]`` field rejects the value.
It also merges its sources with a plain dict update, in which a list from a
lower source is replaced as a whole, so even a converted list could not fill in
a single field of an entry that ``config.yml`` defines.

``LayeredSettingsSource`` wraps the environment, ``.env`` and YAML sources and
merges their output itself, guided by the field annotations of the settings
model:

* A complete list (a YAML list, or a JSON list in one env var) replaces the
  list of the sources below it.
* Indexed entries (``NAME__0__FIELD``, ``NAME__1=...``) patch the list below
  them entry by entry. An object entry merges field by field, a scalar entry is
  replaced, the index one past the end appends. Indexes must be consecutive.
* Inside one env source, indexed entries patch a JSON list given for the same
  setting.
* Objects and dictionaries merge key by key, as pydantic-settings does.
"""

import json
import types
from collections.abc import Mapping, MutableSequence, Sequence
from collections.abc import Set as AbstractSet
from typing import Annotated, Any, Dict, Optional, Tuple, Type, Union, get_args, get_origin

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    EnvSettingsSource,
    PydanticBaseSettingsSource,
    SettingsError,
)
from pydantic_settings.sources.types import EnvNoneType

_MISSING: Any = object()

_LIST_ORIGINS = (list, set, frozenset, Sequence, MutableSequence, AbstractSet)
_DICT_ORIGINS = (dict, Dict, Mapping)

# Shapes an annotation can have for the purpose of merging.
_LIST = "list"
_MODEL = "model"
_DICT = "dict"
_OTHER = "other"


# ── Annotation helpers ────────────────────────────────────────────────────────


def _union_arms(annotation: Any) -> list:
    """The non-None arms of an annotation, with ``Annotated`` stripped."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in (Union, types.UnionType):
        arms = []
        for arg in get_args(annotation):
            if arg is not type(None):
                arms.extend(_union_arms(arg))
        return arms
    return [annotation]


def _shape(annotation: Any) -> Tuple[str, Any]:
    """Classify an annotation as list, model, dict or other.

    Returns the shape and the inner annotation that matters for it: the item
    type of a list, the class of a model, the value type of a dict.
    """
    arms = _union_arms(annotation)
    for arm in arms:
        origin = get_origin(arm)
        if arm in (list, set, frozenset, tuple):
            return _LIST, Any
        if origin in _LIST_ORIGINS:
            args = get_args(arm)
            return _LIST, args[0] if args else Any
        if origin is tuple:
            args = get_args(arm)
            if len(args) == 2 and args[1] is Ellipsis:
                return _LIST, args[0]
    for arm in arms:
        if isinstance(arm, type) and issubclass(arm, BaseModel):
            return _MODEL, arm
    for arm in arms:
        origin = get_origin(arm)
        if arm is dict or origin in _DICT_ORIGINS:
            args = get_args(arm)
            return _DICT, args[1] if len(args) == 2 else Any
    return _OTHER, None


def _allows_plain_string(annotation: Any) -> bool:
    return any(arm is str or arm is Any for arm in _union_arms(annotation))


def _field_key(name: str, field: FieldInfo) -> str:
    """The key a source uses for a field: its alias when it has a plain one."""
    if isinstance(field.validation_alias, str):
        return field.validation_alias
    if isinstance(field.alias, str):
        return field.alias
    return name


def _model_key(
    model: Type[BaseModel], key: Any, case_sensitive: bool
) -> Tuple[Any, Any]:
    """Match ``key`` to a field of ``model``.

    Returns the canonical key and the field annotation, or the key unchanged
    and ``None`` when the model has no such field (pydantic decides what an
    unknown key means).
    """
    if not isinstance(key, str):
        return key, None
    wanted = key if case_sensitive else key.lower()
    for name, field in model.model_fields.items():
        canonical = _field_key(name, field)
        for candidate in {name, canonical}:
            if (candidate if case_sensitive else candidate.lower()) == wanted:
                return canonical, field.annotation
    return key, None


def _join(path: str, key: Any) -> str:
    return f"{path}__{key}" if path else str(key)


# ── Index keys ────────────────────────────────────────────────────────────────


def _index(key: Any, path: str) -> int:
    if isinstance(key, int) and not isinstance(key, bool) and key >= 0:
        return key
    if isinstance(key, str) and key.isascii() and key.isdigit():
        return int(key)
    raise SettingsError(
        f"{path}: {key!r} is not a list index. Entries of a list setting are "
        f"numbered from 0, for example {path}__0, {path}__1."
    )


def _indexed_entries(patch: Mapping, path: str) -> Dict[int, Any]:
    entries: Dict[int, Any] = {}
    for key, value in patch.items():
        index = _index(key, path)
        if index in entries:
            raise SettingsError(
                f"{path}: index {index} is given more than once "
                f"(check for leading zeros such as {path}__0{index})."
            )
        entries[index] = value
    return entries


# ── Preparing env-derived values ──────────────────────────────────────────────


def prepare_env_value(
    value: Any, annotation: Any, path: str, case_sensitive: bool, top: bool = False
) -> Any:
    """Normalise a value that came from environment variables.

    * ``null`` markers become ``None``.
    * Strings at a list, dict or object position below the top level are
      decoded as JSON. pydantic-settings does that only where it can resolve
      the target type, which it cannot inside list entries.
    * Index keys of list positions become ints (and are validated).
    * Object keys are matched to the field names.
    """
    if isinstance(value, EnvNoneType):
        return None
    shape, inner = _shape(annotation)
    if isinstance(value, str):
        if top or shape == _OTHER or _allows_plain_string(annotation):
            return value
        try:
            value = json.loads(value)
        except ValueError:
            raise SettingsError(
                f"{path}: expected a JSON {'list' if shape == _LIST else 'object'}, "
                "but the value is not valid JSON."
            ) from None
    if not isinstance(value, Mapping):
        return value
    if shape == _LIST:
        return {
            index: prepare_env_value(item, inner, _join(path, index), case_sensitive)
            for index, item in _indexed_entries(value, path).items()
        }
    if shape == _MODEL:
        out: Dict[Any, Any] = {}
        for key, item in value.items():
            canonical, field_annotation = _model_key(inner, key, case_sensitive)
            out[canonical] = prepare_env_value(
                item, field_annotation, _join(path, canonical), case_sensitive
            )
        return out
    return {
        key: prepare_env_value(
            item, inner if shape == _DICT else None, _join(path, key), case_sensitive
        )
        for key, item in value.items()
    }


# ── Merging layers ────────────────────────────────────────────────────────────


def merge_layer(
    base: Any, patch: Any, annotation: Any, path: str, case_sensitive: bool
) -> Any:
    """Lay ``patch`` (higher priority) over ``base`` (lower priority).

    ``base`` may be ``_MISSING`` when no lower source set the value.
    """
    shape, inner = _shape(annotation)

    if shape == _LIST and isinstance(patch, Mapping):
        if base is _MISSING or base is None:
            items: list = []
        elif isinstance(base, Mapping):
            items = merge_layer(_MISSING, base, annotation, path, case_sensitive)
        elif isinstance(base, (list, tuple, set, frozenset)):
            items = list(base)
        else:
            raise SettingsError(
                f"{path}: indexed entries ({path}__0, ...) extend a list, but a "
                f"lower-priority source sets this setting to a {type(base).__name__}."
            )
        lower_length = len(items)
        for index, item in sorted(_indexed_entries(patch, path).items()):
            if index < len(items):
                items[index] = merge_layer(
                    items[index], item, inner, _join(path, index), case_sensitive
                )
            elif index == len(items):
                items.append(
                    merge_layer(_MISSING, item, inner, _join(path, index), case_sensitive)
                )
            else:
                origin = (
                    f"the list below it has {lower_length} "
                    f"entr{'y' if lower_length == 1 else 'ies'} (from config.yml, "
                    "a .env file or a JSON value)"
                    if lower_length
                    else "nothing else defines entries for it"
                )
                raise SettingsError(
                    f"{path}: entry {index} is set but entry {len(items)} is missing. "
                    f"Indexed entries must be consecutive and start at 0; {origin}."
                )
        return items

    if shape == _MODEL and isinstance(patch, Mapping):
        out: Dict[Any, Any] = {}
        for layer in (base if isinstance(base, Mapping) else {}, patch):
            for key, item in layer.items():
                canonical, field_annotation = _model_key(inner, key, case_sensitive)
                out[canonical] = merge_layer(
                    out.get(canonical, _MISSING),
                    item,
                    field_annotation,
                    _join(path, canonical),
                    case_sensitive,
                )
        return out

    if isinstance(patch, Mapping) and (shape == _DICT or isinstance(base, Mapping)):
        out = dict(base) if isinstance(base, Mapping) else {}
        for key, item in patch.items():
            out[key] = merge_layer(
                out.get(key, _MISSING),
                item,
                inner if shape == _DICT else None,
                _join(path, key),
                case_sensitive,
            )
        return out

    return patch


# ── The source ────────────────────────────────────────────────────────────────


class LayeredSettingsSource(PydanticBaseSettingsSource):
    """Several settings sources merged with list-aware rules (see module doc).

    ``sources`` are given highest priority first, like the tuple returned
    from ``settings_customise_sources``.
    """

    def __init__(
        self, settings_cls: Type[BaseSettings], *sources: PydanticBaseSettingsSource
    ):
        super().__init__(settings_cls)
        self.sources = sources
        self.case_sensitive = bool(self.config.get("case_sensitive", False))

    def get_field_value(
        self, field: FieldInfo, field_name: str
    ) -> Tuple[Any, str, bool]:
        # Unused: __call__ assembles the whole state from the wrapped sources.
        return None, field_name, False

    def _set_current_state(self, state: Dict[str, Any]) -> None:
        super()._set_current_state(state)
        for source in self.sources:
            source._set_current_state(state)

    def _set_settings_sources_data(self, states: Dict[str, Dict[str, Any]]) -> None:
        super()._set_settings_sources_data(states)
        for source in self.sources:
            source._set_settings_sources_data(states)

    def _read_env_source(self, source: EnvSettingsSource) -> Dict[str, Any]:
        state = dict(source())
        for name, field in self.settings_cls.model_fields.items():
            key = _field_key(name, field)
            if key not in state:
                continue
            value = state[key]
            if _shape(field.annotation)[0] == _LIST and (
                value is None or isinstance(value, list)
            ):
                # pydantic-settings drops indexed vars when the same source
                # also holds a whole value for the setting. Apply them on top.
                patch = source.explode_env_vars(name, field, source.env_vars)
                if patch:
                    if value is None:
                        raise SettingsError(
                            f"{key}: the setting is set to null and also has "
                            f"indexed entries ({key}__0, ...). Use one or the other."
                        )
                    patch = prepare_env_value(
                        patch, field.annotation, key, self.case_sensitive
                    )
                    value = merge_layer(
                        value, patch, field.annotation, key, self.case_sensitive
                    )
            state[key] = prepare_env_value(
                value, field.annotation, key, self.case_sensitive, top=True
            )
        return state

    def __call__(self) -> Dict[str, Any]:
        merged: Optional[Dict[str, Any]] = {}
        for source in reversed(self.sources):
            if isinstance(source, EnvSettingsSource):
                state = self._read_env_source(source)
            else:
                state = source()
            merged = merge_layer(
                merged, state, self.settings_cls, "", self.case_sensitive
            )
        return merged or {}

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({', '.join(map(repr, self.sources))})"
