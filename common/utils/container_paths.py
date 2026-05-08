from __future__ import annotations

import re
import uuid


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_OPAQUE_NAMESPACE = uuid.UUID("a1b2c3d4-e5f6-7890-abcd-ef1234567890")


def sanitize_container_path_token(value: str) -> str:
    normalized = _NON_ALNUM_RE.sub("_", str(value or "").strip().lower()).strip("_")
    return normalized or "root"


def opaque_token(value: str) -> str:
    """Deterministic opaque UUID from any string — hides semantics like CVE IDs."""
    return str(uuid.uuid5(_OPAQUE_NAMESPACE, str(value or "")))[:12]
