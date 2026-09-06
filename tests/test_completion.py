import pytest

from sh41_local.tui.completion import DirectorySuggester, complete_directory


def test_directory_completion(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    for name in ("Projects", "Project two", ".private"):
        (tmp_path / name).mkdir()
    (tmp_path / "Project file").write_text("not a directory")
    (tmp_path / "link").symlink_to(tmp_path / "Projects", target_is_directory=True)
    assert complete_directory("Pro") == "Project two/"
    assert complete_directory("Project t") == "Project two/"
    assert complete_directory("./Pro") == "./Project two/"
    assert complete_directory("../" + tmp_path.name + "/Pro") == "../" + tmp_path.name + "/Project two/"
    assert complete_directory(str(tmp_path) + "/Pro") == str(tmp_path) + "/Project two/"
    assert complete_directory("~") == "~/"
    assert complete_directory("~/Pro") == "~/Project two/"
    assert complete_directory("./") == "./Project two/"
    assert complete_directory(".p") == ".private/"
    assert complete_directory("lin") == "link/"
    assert complete_directory("project") is None
    assert complete_directory("") is None
    assert complete_directory("missing/child") is None
    assert complete_directory("Project file/") is None
    assert complete_directory("\0/") is None


@pytest.mark.asyncio
async def test_directory_suggester_does_not_cache_filesystem(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    suggester = DirectorySuggester()
    assert suggester.case_sensitive and suggester.cache is None
    assert await suggester.get_suggestion("Pro") is None
    (tmp_path / "Projects").mkdir()
    assert await suggester.get_suggestion("Pro") == "Projects/"


def test_unreadable_directory_is_ignored(monkeypatch):
    def denied(path):
        raise PermissionError("denied")

    monkeypatch.setattr("sh41_local.tui.completion.os.scandir", denied)
    assert complete_directory("/private/no") is None
