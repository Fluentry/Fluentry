"""Download model artifacts from Hugging Face.

A port of `ModelDownloader`. The important behaviour is the corruption guard
from issue #353: a corporate proxy or secure web gateway can answer a model
download with an HTML block page and HTTP 200. Persisting that markup as
`model.onnx` caches a permanently broken model, so any payload that *begins*
with markup is rejected — on download, and again for files already cached
from before the check existed.

The detector is deliberately narrow. No artifact this downloader fetches
legitimately starts with `<`: ONNX and GGUF payloads are binary, vocabularies
and manifests are JSON starting with `{` or `[`. An embedded `<pad>` token
inside a JSON vocabulary must not trip it — only a leading `<` followed by a
markup-ish byte counts.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Sequence

SNIFF_BYTES = 512
UTF8_BOM = b"\xef\xbb\xbf"
ASCII_WHITESPACE = b" \t\n\r"

HUGGING_FACE_ENDPOINT = "https://huggingface.co"


class ModelDownloadError(Exception):
    pass


class ModelDownloadCancelled(Exception):
    pass


def looks_like_markup(data: bytes) -> bool:
    """True when the payload begins with an HTML/XML marker.

    A UTF-8 BOM and leading ASCII whitespace are ignored first, since a proxy
    page often arrives with both.
    """
    prefix = bytes(data[:SNIFF_BYTES])
    if prefix.startswith(UTF8_BOM):
        prefix = prefix[len(UTF8_BOM) :]
    prefix = prefix.lstrip(ASCII_WHITESPACE)

    if len(prefix) < 2 or prefix[0] != 0x3C:  # "<"
        return False
    # Requiring a markup-ish second byte avoids rejecting a text artifact that
    # merely contains a stray "<".
    second = prefix[1]
    is_ascii_letter = (0x41 <= second <= 0x5A) or (0x61 <= second <= 0x7A)
    return is_ascii_letter or second in (0x21, 0x3F, 0x2F)  # "!", "?", "/"


def cached_file_is_markup(path: Path) -> bool:
    """Byte-sniff a file already on disk.

    Returns False on any read error: an unreadable file is never deleted on
    uncertainty.
    """
    try:
        with open(path, "rb") as handle:
            return looks_like_markup(handle.read(SNIFF_BYTES))
    except OSError:
        return False


def cached_payload_contains_markup(root: Path, relative_paths: Iterable[str]) -> bool:
    """The tree analogue, for a preflight that would otherwise trust existence.

    A directory is scanned recursively; a missing path or an unreadable file
    is skipped, so a valid cache is never reported corrupt.
    """
    root = Path(root)
    for relative_path in relative_paths:
        path = root / relative_path
        if not path.exists():
            continue
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.name.startswith("."):
                    continue
                if child.is_file() and cached_file_is_markup(child):
                    return True
        elif cached_file_is_markup(path):
            return True
    return False


def validate_downloaded_file(
    path: Path,
    content_type: str | None = None,
    expected_bytes: int | None = None,
    relative_path: str = "",
) -> None:
    """Reject a payload that is not the model file we asked for."""
    if expected_bytes is not None and expected_bytes > 0:
        actual_bytes = path.stat().st_size
        if actual_bytes != expected_bytes:
            raise ModelDownloadError(
                f"Could not download {relative_path}: the received file size "
                "did not match the server response."
            )

    if content_type:
        lowered = content_type.lower()
        if "text/html" in lowered or "text/xml" in lowered or "application/xml" in lowered:
            raise _invalid_content_error(
                relative_path, f"the server returned a markup page (Content-Type: {content_type})"
            )

    # Sniff the leading bytes too: markup can arrive without a markup type.
    with open(path, "rb") as handle:
        prefix = handle.read(SNIFF_BYTES)
    if looks_like_markup(prefix):
        raise _invalid_content_error(
            relative_path,
            "the downloaded file is an HTML/markup document, not the expected model data",
        )


def _invalid_content_error(relative_path: str, detail: str) -> ModelDownloadError:
    return ModelDownloadError(
        f"Could not download {relative_path}: {detail}. "
        "A network proxy or firewall may be blocking model downloads."
    )


@dataclass(frozen=True)
class ModelItem:
    path: str
    is_directory: bool = False


def artifact_is_complete(path: Path, is_directory: bool) -> bool:
    """A present artifact only counts when it has content and is not markup."""
    if is_directory:
        if not path.is_dir():
            return False
        return any(child.is_file() for child in path.rglob("*"))
    if not path.is_file():
        return False
    if path.stat().st_size <= 0:
        return False
    return not cached_file_is_markup(path)


def artifacts_are_complete(root: Path, items: Sequence[ModelItem]) -> bool:
    return all(
        artifact_is_complete(Path(root) / item.path, item.is_directory) for item in items
    )


@dataclass
class DownloadProgress:
    relative_path: str
    received_bytes: int
    total_bytes: int | None

    @property
    def fraction(self) -> float | None:
        if not self.total_bytes:
            return None
        return min(1.0, self.received_bytes / self.total_bytes)


class HuggingFaceModelDownloader:
    """Fetches files from a Hugging Face repository into a local cache."""

    def __init__(
        self,
        repository: str,
        destination_root: Path,
        revision: str = "main",
        endpoint: str = HUGGING_FACE_ENDPOINT,
        session=None,
        token: str | None = None,
    ) -> None:
        self.repository = repository
        self.destination_root = Path(destination_root)
        self.revision = revision
        self.endpoint = endpoint.rstrip("/")
        self._session = session
        self.token = token or os.environ.get("HF_TOKEN")

    def file_url(self, relative_path: str) -> str:
        return f"{self.endpoint}/{self.repository}/resolve/{self.revision}/{relative_path}"

    def _ensure_session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def needs_download(self, relative_path: str, is_directory: bool = False) -> bool:
        destination = self.destination_root / relative_path
        if artifact_is_complete(destination, is_directory):
            return False
        if destination.exists():
            # A corrupt cached payload is deleted so the retry is a real one.
            try:
                if destination.is_dir():
                    shutil.rmtree(destination)
                else:
                    destination.unlink()
            except OSError:
                pass
        return True

    def download_file(
        self,
        relative_path: str,
        on_progress: Callable[[DownloadProgress], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> Path:
        destination = self.destination_root / relative_path
        if not self.needs_download(relative_path):
            return destination

        session = self._ensure_session()
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        try:
            response = session.get(self.file_url(relative_path), headers=headers, stream=True, timeout=60)
        except Exception as error:
            raise ModelDownloadError(f"Could not download {relative_path}: {error}") from error
        if response.status_code >= 400:
            raise ModelDownloadError(
                f"Could not download {relative_path}: HTTP {response.status_code}."
            )

        total_header = response.headers.get("Content-Length")
        total_bytes = int(total_header) if total_header and total_header.isdigit() else None
        received = 0

        destination.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            delete=False, dir=str(destination.parent), prefix=".download-"
        )
        temporary_path = Path(handle.name)
        try:
            with handle:
                for chunk in response.iter_content(chunk_size=1 << 16):
                    if is_cancelled is not None and is_cancelled():
                        raise ModelDownloadCancelled()
                    if not chunk:
                        continue
                    handle.write(chunk)
                    received += len(chunk)
                    if on_progress is not None:
                        on_progress(DownloadProgress(relative_path, received, total_bytes))
            validate_downloaded_file(
                temporary_path,
                content_type=response.headers.get("Content-Type"),
                expected_bytes=total_bytes,
                relative_path=relative_path,
            )
            if destination.exists():
                destination.unlink()
            os.replace(temporary_path, destination)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        return destination

    def download_all(
        self,
        items: Sequence[ModelItem],
        on_progress: Callable[[DownloadProgress], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        for item in items:
            if is_cancelled is not None and is_cancelled():
                raise ModelDownloadCancelled()
            self.download_file(item.path, on_progress=on_progress, is_cancelled=is_cancelled)
