"""Operator forget for a local ``.fafm`` or a hosted soul body.

``forget`` is an SDK method. It is not a tool the model can call.
Criteria combine with AND. A bare string fact has no id, tags, or
timestamp, so id, tag, and time filters leave it in place. ``wipe``
removes every fact and clears ``index``. A narrower forget drops index
entries that name a removed id, and leaves the other index lines.
Rewriting YAML drops comments and writes date-times back as RFC 3339.
Unknown keys and their order are kept. The file is not deleted, and
this module never calls ``delete_soul``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import yaml

_ENTRY_MARKS = ("✓", "•", "☐", "?")
_ISO_IN_LINE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?"
)
_ID_IN_LINE = re.compile(r"\bid\s*[:=]\s*(\S+)")
_TAGS_IN_LINE = re.compile(r"\[([^\]]*)\]\s*$")
_SCAFFOLD = re.compile(
    r"^\s*(?:#\s*(?:Fact|Note|Todo|Question)s?:.*|"
    r"\[(?:facts|notes|todos|questions|memory)\])\s*$",
    re.IGNORECASE,
)


class FAFForgetError(RuntimeError):
    """Raised when forget is refused before it changes a soul.

    A namepoint that does not match the bound soul, or criteria with
    nothing set, raise this. Neither path writes.
    """


@dataclass(frozen=True)
class ForgetCriteria:
    """What to remove. Unset fields are not filters. Set fields AND.

    ``wipe=True`` removes every fact (and, on YAML, the index) and
    ignores the other fields.
    """

    entry_id: str | None = None
    tag: str | None = None
    start: datetime | str | None = None
    end: datetime | str | None = None
    wipe: bool = False

    def is_empty(self) -> bool:
        return (
            self.entry_id is None
            and self.tag is None
            and self.start is None
            and self.end is None
            and not self.wipe
        )


def apply_forget(text: str, criteria: ForgetCriteria) -> tuple[str, int]:
    """Return ``(new_text, removed)``. ``removed == 0`` means unchanged text."""
    if criteria.is_empty():
        raise FAFForgetError(
            "forget needs an entry id, a tag, a time range, or wipe=True."
        )
    doc = _load_yaml_soul(text)
    if doc is not None:
        new_text, removed = _forget_yaml(doc, criteria)
        if removed == 0:
            return text, 0
        return new_text, removed
    return _forget_lines(text, criteria)


class _SoulLoader(yaml.SafeLoader):
    """SafeLoader that leaves timestamps as strings.

    PyYAML's default resolver turns ``2026-11-01T10:00:00Z`` into a
    datetime, and ``safe_dump`` then writes ``2026-11-01 10:00:00+00:00``.
    A kept fact must stay RFC 3339.
    """


_SoulLoader.yaml_implicit_resolvers = {
    key: [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:timestamp"
    ]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class _SoulDumper(yaml.SafeDumper):
    """Dump datetimes as RFC 3339 if one is still in the document."""


def _rfc3339(when: datetime) -> str:
    utc = _as_utc(when)
    if utc.microsecond:
        fraction = f"{utc.microsecond:06d}".rstrip("0")
        return utc.strftime("%Y-%m-%dT%H:%M:%S") + f".{fraction}Z"
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _represent_datetime(dumper: yaml.SafeDumper, data: datetime):
    return dumper.represent_scalar("tag:yaml.org,2002:str", _rfc3339(data))


_SoulDumper.add_representer(datetime, _represent_datetime)


def _load_yaml_soul(text: str) -> dict[str, Any] | None:
    try:
        doc = yaml.load(text, Loader=_SoulLoader)  # noqa: S506 — SafeLoader subclass
    except yaml.YAMLError:
        return None
    if isinstance(doc, dict) and "memory" in doc:
        return doc
    return None


def split_hosted_banner(raw: str) -> str:
    """Drop an old get_soul banner, and leave a real soul intact.

    Current MCPaaS ``get_soul`` returns the stored soul text. It does
    not add a banner (``handler.ts``). Some captured reads still have
    a banner before the first ``\\n---\\n``. That banner is removed
    only when the remainder is a soul document and the banner is not.
    A soul that itself contains that separator is returned whole.
    """
    sep = "\n---\n"
    if sep not in raw:
        return raw
    head, tail = raw.split(sep, 1)
    if _load_yaml_soul(tail) is not None and _load_yaml_soul(head) is None:
        return tail
    return raw


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _timestamp_of(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _as_utc(value.strip())
    except ValueError:
        return None


def _in_range(when: datetime | None, criteria: ForgetCriteria) -> bool:
    """Inclusive. A missing timestamp does not match a time filter."""
    if criteria.start is None and criteria.end is None:
        return True
    if when is None:
        return False
    if criteria.start is not None and when < _as_utc(criteria.start):
        return False
    if criteria.end is not None and when > _as_utc(criteria.end):
        return False
    return True


def _fact_matches(fact: Any, criteria: ForgetCriteria) -> bool:
    if criteria.wipe:
        return True
    if not isinstance(fact, dict):
        return False
    if criteria.entry_id is not None and str(fact.get("id", "")) != criteria.entry_id:
        return False
    if criteria.tag is not None:
        tags = fact.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        if criteria.tag not in tags:
            return False
    if not _in_range(_timestamp_of(fact.get("timestamp")), criteria):
        return False
    # A time-only filter must not delete a fact that simply lacked a timestamp.
    # _in_range already returned False for that. Reaching here with no id and
    # no tag means the timestamp was inside the range.
    if (
        criteria.entry_id is None
        and criteria.tag is None
        and criteria.start is None
        and criteria.end is None
    ):
        return False
    return True


def _dump(doc: dict[str, Any]) -> str:
    """Serialize a soul. Comments in the original YAML are not kept.

    Date-times are written as RFC 3339. The loader keeps them as
    strings so a kept fact is not rewritten into a space-separated time.
    """
    return yaml.dump(
        doc,
        Dumper=_SoulDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )


def _forget_yaml(doc: dict[str, Any], criteria: ForgetCriteria) -> tuple[str, int]:
    memory = doc.get("memory")
    if not isinstance(memory, dict):
        memory = {}
        doc["memory"] = memory
    facts = list(memory.get("facts") or [])
    kept: list[Any] = []
    removed = 0
    removed_ids: set[str] = set()
    for fact in facts:
        if _fact_matches(fact, criteria):
            removed += 1
            fact_id = _removed_id(fact)
            if fact_id is not None:
                removed_ids.add(fact_id)
        else:
            kept.append(fact)
    memory["facts"] = kept
    if criteria.wipe and "index" in doc:
        index = doc.get("index") or []
        if isinstance(index, list) and index:
            removed += len(index)
        doc["index"] = []
    elif removed_ids and isinstance(doc.get("index"), list):
        kept_index: list[Any] = []
        for item in doc["index"]:
            if _index_mentions(item, removed_ids):
                removed += 1
            else:
                kept_index.append(item)
        doc["index"] = kept_index
    return _dump(doc), removed


def _removed_id(fact: Any) -> str | None:
    if isinstance(fact, dict) and fact.get("id"):
        return str(fact["id"])
    return None


def _index_mentions(item: Any, removed_ids: set[str]) -> bool:
    """True when an index line names a fact id this forget removed."""
    if isinstance(item, str):
        head = re.split(r"\s+[—–-]\s+", item, maxsplit=1)[0].strip()
        return head in removed_ids
    if isinstance(item, dict):
        for key in ("id", "entry_id"):
            value = item.get(key)
            if value is not None and str(value) in removed_ids:
                return True
    return False


def _is_entry_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and stripped[0] in _ENTRY_MARKS


def _line_tags(line: str) -> list[str]:
    match = _TAGS_IN_LINE.search(line.strip())
    if not match:
        return []
    return [part.strip() for part in match.group(1).split(",") if part.strip()]


def _line_timestamp(line: str) -> datetime | None:
    match = _ISO_IN_LINE.search(line)
    if not match:
        return None
    return _timestamp_of(match.group(0))


def _line_id(line: str) -> str | None:
    match = _ID_IN_LINE.search(line)
    if not match:
        return None
    return match.group(1).rstrip(".,;")


def _line_matches(line: str, criteria: ForgetCriteria) -> bool:
    if criteria.wipe:
        return True
    if criteria.entry_id is not None and _line_id(line) != criteria.entry_id:
        return False
    if criteria.tag is not None and criteria.tag not in _line_tags(line):
        return False
    if not _in_range(_line_timestamp(line), criteria):
        return False
    if (
        criteria.entry_id is None
        and criteria.tag is None
        and criteria.start is None
        and criteria.end is None
    ):
        return False
    return True


def _forget_lines(text: str, criteria: ForgetCriteria) -> tuple[str, int]:
    kept: list[str] = []
    removed = 0
    ends_with_newline = text.endswith("\n")
    lines = text.splitlines()
    for line in lines:
        if criteria.wipe and _SCAFFOLD.match(line):
            removed += 1
            continue
        if _is_entry_line(line) and _line_matches(line, criteria):
            removed += 1
            continue
        kept.append(line)
    if removed == 0:
        return text, 0
    body = "\n".join(kept)
    if ends_with_newline or body:
        body += "\n" if ends_with_newline else ""
    return body, removed
