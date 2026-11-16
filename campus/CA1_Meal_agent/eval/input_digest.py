"""Portable UTF-8 text hashes for evaluation inputs (not user-data revision hashes)."""
import hashlib
from pathlib import Path


def text_sha256(path):
    # Git's LF/CRLF checkout conversion does not change the evaluation input.
    return hashlib.sha256(Path(path).read_text(encoding="utf-8-sig").encode("utf-8")).hexdigest()
