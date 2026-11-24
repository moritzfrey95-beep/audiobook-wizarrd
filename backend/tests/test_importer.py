from abw.importer import clean, metadata


def test_metadata_carries_series_number_and_asin():
    m = metadata(dict(title="Mockingjay", subtitle=None, author="Suzanne Collins", narrator="Carolyn McCormick, Second Voice",
                      series="The Hunger Games", sequence="3", release_date="2010-08-24", asin="B003YVPV2O", lang="en"))
    assert m["series"] == ["The Hunger Games #3"] and m["asin"] == "B003YVPV2O"
    assert m["authors"] == ["Suzanne Collins"] and len(m["narrators"]) == 2
    assert m["publishedYear"] == "2010" and m["language"] == "English"


def test_folder_names_have_no_path_separators():
    assert "/" not in clean("Book 3 - A/B: C?") and clean(" x . ") == "x"


def test_series_field_with_several_series_uses_the_first():
    from abw.abs import split_series
    assert split_series("Star Wars #53, Star Wars: The Last Jedi") == ("Star Wars", "53")
    assert split_series("Syl #1") == ("Syl", "1")
    assert split_series("First Law World") == ("First Law World", None)
    assert split_series("Me, Myself #3") == ("Me, Myself", "3")


def test_leftover_part_folder_goes_to_trash(tmp_path, monkeypatch):
    from abw import config, importer
    lib, trash = tmp_path / "lib", tmp_path / "trash"
    monkeypatch.setitem(config.LIBRARY_ROOT, "en", lib)
    monkeypatch.setattr(config, "LIBRARY_TRASH", trash)
    target = lib / "Dual Class" / "Book 3 - Dual Class 3"
    stale = target.with_name("." + target.name + ".abw-teil")
    stale.mkdir(parents=True)
    (stale / "half.m4b").write_bytes(b"x" * 10)  # Kopieren durch Neustart abgebrochen

    @importer._frees_teil
    def attempt(fail):
        tmp = importer.teil_dir(target, "en")
        if fail:
            raise IOError("Kopie unvollständig")
        with __import__("pytest").raises(ValueError, match="läuft gerade"):
            importer.teil_dir(target, "en")  # derselbe Band zweimal gleichzeitig
        tmp.rename(target)

    with __import__("pytest").raises(IOError):
        attempt(True)
    attempt(False)  # der Rest des Fehlversuchs blockiert nicht
    assert target.is_dir() and not stale.exists()
    kept = sorted(p.name for p in trash.rglob("half.m4b"))
    assert len(kept) == 1  # der erste Rest liegt im Papierkorb, nichts gelöscht
    assert len(list(trash.rglob("*.abw-teil-*"))) == 2
