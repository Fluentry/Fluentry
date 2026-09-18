"""Port of the model-download corruption guard (issue #353)."""

import json

import pytest

from fluentry.services.model_downloader import (
    ModelDownloadError,
    ModelItem,
    artifact_is_complete,
    artifacts_are_complete,
    cached_file_is_markup,
    cached_payload_contains_markup,
    looks_like_markup,
    validate_downloaded_file,
)

BOM = b"\xef\xbb\xbf"


def test_looks_like_markup_rejects_markup_variants():
    rejected = [
        '<!DOCTYPE html><html lang="en"><head></head></html>',
        "<html><body>Blocked by corporate proxy</body></html>",
        "<script>window.location='https://proxy'</script>",
        "<head><title>Access Denied</title></head>",
        "<body>Forbidden</body>",
        '<meta http-equiv="refresh" content="0">',
        "<!-- corporate gateway notice -->",
        '<?xml version="1.0" encoding="UTF-8"?><error>blocked</error>',
        "</html>",
        '<!doctype HTML PUBLIC "-//W3C//DTD HTML 4.01//EN">',
    ]
    for markup in rejected:
        assert looks_like_markup(markup.encode("utf-8")), markup


def test_looks_like_markup_rejects_leading_whitespace_and_bom_variants():
    assert looks_like_markup(b"   \n\t<!DOCTYPE html>")
    assert looks_like_markup(b"\r\n  <html>")
    assert looks_like_markup(BOM + b"<html>")
    assert looks_like_markup(BOM + b'  \n<?xml version="1.0"?>')


def test_looks_like_markup_accepts_model_artifacts():
    # An embedded "<pad>" token must not trip the detector; only a leading "<".
    assert not looks_like_markup(b'{"0": "<pad>", "1": "a"}')
    assert not looks_like_markup(b"[1, 2, 3]")
    assert not looks_like_markup(b"program(1.0)\n[buildInfo = ...]")
    assert not looks_like_markup(bytes([0xCF, 0xFA, 0xED, 0xFE, 0x07, 0x00]))
    assert not looks_like_markup(bytes([0x00, 0x00, 0x01, 0x3C, 0x68]))
    assert not looks_like_markup(b"")
    assert not looks_like_markup(b"< not markup")
    assert not looks_like_markup(b"<")
    # GGUF and ONNX magic bytes.
    assert not looks_like_markup(b"GGUF\x03\x00\x00\x00")
    assert not looks_like_markup(b"\x08\x07\x12\x0bonnxruntime")


def test_validate_downloaded_file_rejects_markup_and_accepts_json(tmp_path):
    html_path = tmp_path / "model.onnx"
    html_path.write_bytes(b"<!DOCTYPE html><html><body>Blocked</body></html>")
    with pytest.raises(ModelDownloadError):
        validate_downloaded_file(html_path, relative_path="model.onnx")

    json_path = tmp_path / "vocab.json"
    json_path.write_bytes(b'{"0": "<pad>", "1": "the"}')
    validate_downloaded_file(json_path, relative_path="vocab.json")


def test_validate_downloaded_file_rejects_a_markup_content_type(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"\x08\x07binary")
    with pytest.raises(ModelDownloadError, match="markup page"):
        validate_downloaded_file(path, content_type="text/html; charset=utf-8", relative_path="model.onnx")


def test_validate_downloaded_file_rejects_a_truncated_download(tmp_path):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"12345")
    with pytest.raises(ModelDownloadError, match="file size"):
        validate_downloaded_file(path, expected_bytes=99, relative_path="model.onnx")
    validate_downloaded_file(path, expected_bytes=5, relative_path="model.onnx")


def test_cached_file_is_markup_detects_corrupt_cache_and_accepts_model_data(tmp_path):
    html_path = tmp_path / "model.onnx"
    html_path.write_bytes(b"<!DOCTYPE html><html><body>Blocked by proxy</body></html>")
    assert cached_file_is_markup(html_path)

    json_path = tmp_path / "vocab.json"
    json_path.write_bytes(b'{"0": "<pad>", "1": "the"}')
    assert not cached_file_is_markup(json_path)

    # An unreadable or missing path is treated as valid: never delete on doubt.
    assert not cached_file_is_markup(tmp_path / "does-not-exist.bin")


def test_cached_payload_contains_markup_scans_a_present_artifact_tree(tmp_path):
    package = tmp_path / "encoder"
    weights = package / "data" / "weights"
    weights.mkdir(parents=True)
    (package / "manifest.json").write_bytes(b'{"fileFormatVersion": "1.0.0"}')
    (weights / "weight.bin").write_bytes(bytes([0x00, 0x01, 0x02]))

    assert not cached_payload_contains_markup(tmp_path, ["encoder", "missing.json"])

    (weights / "weight.bin").write_bytes(b"<html><body>Blocked</body></html>")
    assert cached_payload_contains_markup(tmp_path, ["encoder"])


def test_cached_payload_contains_markup_is_conservative_about_empty_directories(tmp_path):
    (tmp_path / "empty").mkdir()
    # Incompleteness is the existence check's concern, not the markup check's.
    assert not cached_payload_contains_markup(tmp_path, ["empty"])


def test_artifact_completeness_requires_content_and_real_data(tmp_path):
    empty = tmp_path / "empty.onnx"
    empty.write_bytes(b"")
    assert not artifact_is_complete(empty, is_directory=False)

    markup = tmp_path / "markup.onnx"
    markup.write_bytes(b"<html>")
    assert not artifact_is_complete(markup, is_directory=False)

    good = tmp_path / "good.onnx"
    good.write_bytes(b"\x08\x07binary")
    assert artifact_is_complete(good, is_directory=False)

    missing = tmp_path / "nope.onnx"
    assert not artifact_is_complete(missing, is_directory=False)

    directory = tmp_path / "bundle"
    directory.mkdir()
    assert not artifact_is_complete(directory, is_directory=True)
    (directory / "part.bin").write_bytes(b"\x01")
    assert artifact_is_complete(directory, is_directory=True)

    assert artifacts_are_complete(
        tmp_path, [ModelItem("good.onnx"), ModelItem("bundle", is_directory=True)]
    )
    assert not artifacts_are_complete(tmp_path, [ModelItem("markup.onnx")])


def test_needs_download_deletes_a_corrupt_cached_file(tmp_path):
    from fluentry.services.model_downloader import HuggingFaceModelDownloader

    downloader = HuggingFaceModelDownloader("owner/model", destination_root=tmp_path)
    corrupt = tmp_path / "model.onnx"
    corrupt.write_bytes(b"<html>Blocked</html>")

    assert downloader.needs_download("model.onnx")
    assert not corrupt.exists(), "the corrupt cache is removed so the retry is real"

    corrupt.write_bytes(b"\x08\x07binary")
    assert not downloader.needs_download("model.onnx")


def test_file_url_points_at_the_pinned_revision(tmp_path):
    from fluentry.services.model_downloader import HuggingFaceModelDownloader

    downloader = HuggingFaceModelDownloader("owner/model", tmp_path, revision="v1.2")
    assert downloader.file_url("a/b.onnx") == "https://huggingface.co/owner/model/resolve/v1.2/a/b.onnx"
