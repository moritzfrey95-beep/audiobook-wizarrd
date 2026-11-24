"""Tests arbeiten nie mit der echten Datenbank: Datenordner vor dem ersten Import umlenken."""
import os
import tempfile

os.environ["ABW_DATA_DIR"] = tempfile.mkdtemp(prefix="abw-test-")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _spracherkennung_wie_im_image(monkeypatch):
    """Die Tests prüfen die Ansage mit nachgebildetem Whisper; ob faster-whisper hier installiert ist, spielt keine Rolle."""
    from abw import verify
    monkeypatch.setattr(verify, "speech_available", lambda: True)
