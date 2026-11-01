"""Shared, standard-library-only matching and copying for the CLI and GUI.

collect() returns counters and optionally sends one immutable CollectionEvent
per scanned file. Callbacks run synchronously on the caller's thread. Paths in
events are relative to the source/output roots; this module never prints or
accesses a GUI. Filesystem errors propagate to the caller.
"""

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import stat
from typing import Callable, Dict, Optional, Tuple


DEFAULT_MAX_BYTES = 5000000
BINARY_EXTENSIONS = set(
    ".exe .dll .png .jpg .jpeg .gif .bmp .ico .zip .7z .rar .pdf "
    ".doc .docx .xls .xlsx .ppt .pptx .mp3 .mp4 .avi .mov .woff .woff2 "
    ".ttf .otf .class .pyc .so .dylib .bin .iso .dmg .apk".split()
)
ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "latin-1")
URL_RE = re.compile(r"(?<![a-z0-9_])(?:https?|ftp|file)://[^\s<>\"']+", re.I | re.ASCII)
DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# An alphabetic 2-63-character TLD is plausible; no DNS/network lookup is used.
DOMAIN_RE = re.compile(
    r"(?<![a-z0-9_.-])(?:" + DOMAIN_LABEL + r"\.)+[a-z]{2,63}"
    r"(?![a-z0-9_-]|\.[a-z0-9_-])",
    re.I | re.ASCII,
)
IPV4_RE = re.compile(
    r"(?<![a-z0-9_.])(?P<address>(?:[0-9]{1,3}\.){3}[0-9]{1,3})"
    r"(?![a-z0-9_]|\.[a-z0-9_])(?::[0-9]{1,5})?",
    re.I | re.ASCII,
)


@dataclass(frozen=True)
class CollectionProgress:
    scanned: int = 0
    hits: int = 0
    copied: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class CollectionEvent:
    progress: CollectionProgress
    relative_path: Path
    destination: Optional[Path]
    action: str
    reason: str = ""


def contains_address(text: str) -> bool:
    if URL_RE.search(text) or DOMAIN_RE.search(text):
        return True
    return any(
        all(int(octet) <= 255 for octet in match.group("address").split("."))
        for match in IPV4_RE.finditer(text)
    )


def read_candidate(path: Path, max_bytes: int) -> Tuple[Optional[bytes], str]:
    if path.is_symlink():
        return None, "symbolic link"
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return None, "binary extension"
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        return None, "not a regular file"
    if info.st_size > max_bytes:
        return None, "exceeds --max-bytes"
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:  # Also bound files that grow after stat().
        return None, "exceeds --max-bytes"
    if b"\x00" in data[:8192]:
        return None, "NUL in first 8KB"
    return data, ""


def decode_text(data: bytes) -> str:
    for encoding in ENCODINGS:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("Could not decode text")  # latin-1 accepts every byte.


def same_content(path: Path, size: int, digest: bytes) -> bool:
    # A directory, special file, or symlink is a collision, not a copy target.
    if path.is_symlink() or not path.is_file() or path.stat().st_size != size:
        return False
    existing_digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            existing_digest.update(chunk)
    return existing_digest.digest() == digest


def choose_destination(
    desired: Path,
    data: bytes,
    reserved: Dict[Path, Tuple[int, bytes]],
) -> Tuple[Path, bool]:
    signature = (len(data), hashlib.sha256(data).digest())
    index = 0
    while True:
        candidate = desired if index == 0 else desired.with_name(
            "{}__{}{}".format(desired.stem, index, desired.suffix)
        )
        if candidate in reserved:
            if reserved[candidate] == signature:
                return candidate, True
        elif os.path.lexists(candidate):
            if same_content(candidate, *signature):
                return candidate, True
        else:
            # Reserve names in dry runs too, so later hits see planned copies.
            reserved[candidate] = signature
            return candidate, False
        index += 1


def raise_walk_error(error: OSError) -> None:
    raise error


def check_output_subdirectories(out: Path, relative_parent: Path) -> None:
    current = out
    for name in relative_parent.parts:
        current = current / name
        # resolve() also detects Windows junctions on supported Python versions.
        if current.is_symlink() or current.resolve() != current:
            raise ValueError(
                "Output subdirectory is a symbolic link or reparse point: {}".format(current)
            )


def collect(
    src: Path,
    out: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
    dry_run: bool = False,
    on_event: Optional[Callable[[CollectionEvent], None]] = None,
) -> CollectionProgress:
    """Scan/copy once, preserving relative paths and never overwriting files.

    Dry runs create nothing, including directories. Hits include duplicates;
    skipped counts rejected files and duplicate hits, but not non-matching text.
    """
    src = Path(src).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    if not src.is_dir():
        raise NotADirectoryError("Source directory missing or not a directory: {}".format(src))
    if src == out:
        raise ValueError("Source and output directories must be different")
    if out.exists() and not out.is_dir():
        raise NotADirectoryError("Output is not a directory: {}".format(out))
    if max_bytes < 0:
        raise ValueError("max_bytes must be nonnegative")

    scanned = skipped = copied = hits = 0
    reserved = {}  # type: Dict[Path, Tuple[int, bytes]]
    for root, directories, filenames in os.walk(
        src, topdown=True, onerror=raise_walk_error, followlinks=False
    ):
        current = Path(root)
        # Never rescan our output, including when it is nested under src.
        directories[:] = sorted(
            name for name in directories
            if not (current / name).is_symlink()
            and (current / name).resolve() != out
        )
        for name in sorted(filenames):
            path = current / name
            relative = path.relative_to(src)
            scanned += 1
            data, reason = read_candidate(path, max_bytes)
            destination_relative = None
            if data is None:
                skipped += 1
                action = "skipped"
            elif not contains_address(decode_text(data)):
                action = "no match"
            else:
                check_output_subdirectories(out, relative.parent)
                destination, duplicate = choose_destination(out / relative, data, reserved)
                if duplicate:
                    skipped += 1
                    action = "same content; skipped"
                elif dry_run:
                    action = "would copy"
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    check_output_subdirectories(out, relative.parent)
                    # Exclusive creation prevents silently overwriting a collision.
                    handle = destination.open("xb")
                    try:
                        with handle:
                            handle.write(data)
                    except BaseException:
                        # Only remove a file we successfully created ourselves.
                        try:
                            destination.unlink()
                        except OSError:
                            pass  # Preserve the original write/flush error.
                        raise
                    copied += 1
                    action = "copied"
                hits += 1
                destination_relative = destination.relative_to(out)

            if on_event is not None:
                on_event(CollectionEvent(
                    progress=CollectionProgress(scanned, hits, copied, skipped),
                    relative_path=relative,
                    destination=destination_relative,
                    action=action,
                    reason=reason,
                ))

    return CollectionProgress(scanned, hits, copied, skipped)
