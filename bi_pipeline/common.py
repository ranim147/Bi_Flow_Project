from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
import threading
import time
import uuid
from pathlib import Path

_LOG_LOCK = threading.Lock()


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp." + uuid.uuid4().hex)
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    for attempt in range(8):
        try:
            os.replace(temporary, path)
            break
        except PermissionError:
            if attempt == 7:
                raise
            time.sleep(min(.1 * 2**attempt, 1.5))


def log(path, event, **data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOG_LOCK:
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(timestamp=now(), event=event, **data), ensure_ascii=False, allow_nan=False) + "\n")


def inside(root, relative):
    path = (Path(root) / relative).resolve()
    if not path.is_relative_to(Path(root).resolve()):
        raise ValueError("Path escapes the dataset version")
    return path
