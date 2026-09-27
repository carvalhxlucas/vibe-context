import io

import pytest

from vibecontext.ingest import filetypes, storage
from vibecontext.ingest.errors import IngestError


@pytest.mark.parametrize(
    "name, kind, language",
    [
        ("spec.PDF", "pdf", None),
        ("ata.docx", "docx", None),
        ("notes.md", "markdown", None),
        ("readme.txt", "text", None),
        ("app.tsx", "code", "tsx"),
        ("main.go", "code", "go"),
        ("config.yml", "code", "yaml"),
    ],
)
def test_classifies_by_extension(name, kind, language):
    file_type = filetypes.from_filename(name)
    assert (file_type.kind, file_type.language) == (kind, language)


@pytest.mark.parametrize("name", ["virus.exe", "archive.zip", "Makefile", ".env"])
def test_rejects_unsupported_extensions(name):
    with pytest.raises(IngestError, match="Unsupported file type"):
        filetypes.from_filename(name)


def test_rejects_content_that_does_not_match_extension():
    with pytest.raises(IngestError, match="not a PDF"):
        filetypes.confirm_content(filetypes.from_filename("a.pdf"), b"MZ\x90\x00")
    with pytest.raises(IngestError, match="not a Word document"):
        filetypes.confirm_content(filetypes.from_filename("a.docx"), b"%PDF-1.4")
    with pytest.raises(IngestError, match="binary data"):
        filetypes.confirm_content(filetypes.from_filename("a.md"), b"# hi\x00\x01")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("../../etc/passwd.md", "passwd.md"),
        ("..\\..\\windows\\system.ini.txt", "system.ini.txt"),
        ("/abs/path/spec.pdf", "spec.pdf"),
        ("evil\x00name\n.md", "evilname.md"),
    ],
)
def test_display_name_strips_paths_and_control_chars(raw, expected):
    assert storage.display_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "..", "dir/", None])
def test_display_name_rejects_empty_names(raw):
    with pytest.raises(IngestError):
        storage.display_name(raw)


def test_file_path_refuses_to_leave_files_dir(tmp_path):
    assert storage.file_path(tmp_path, "abc.pdf") == (tmp_path / "abc.pdf").resolve()
    for escape in ("../secrets.json", "sub/abc.pdf", "/etc/passwd"):
        with pytest.raises(ValueError):
            storage.file_path(tmp_path, escape)


def test_save_enforces_size_limit_and_leaves_nothing_behind(tmp_path):
    destination = tmp_path / "doc.txt"
    with pytest.raises(storage.UploadTooLarge):
        storage.save(io.BytesIO(b"x" * 2048), destination, max_bytes=1024)
    assert list(tmp_path.iterdir()) == []

    size, digest, head = storage.save(io.BytesIO(b"hello"), destination, max_bytes=1024)
    assert (size, head) == (5, b"hello")
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
