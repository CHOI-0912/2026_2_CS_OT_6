from __future__ import annotations

import os

from ambulance_sim.env_file import load_env_file


def test_load_env_file_reads_values_and_ignores_comments(tmp_path, monkeypatch):
    monkeypatch.delenv("FIRST_TEST_KEY", raising=False)
    monkeypatch.delenv("SECOND_TEST_KEY", raising=False)
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nFIRST_TEST_KEY=alpha\nSECOND_TEST_KEY=\"beta value\"\n",
        encoding="utf-8",
    )

    load_env_file(path)

    assert os.environ["FIRST_TEST_KEY"] == "alpha"
    assert os.environ["SECOND_TEST_KEY"] == "beta value"


def test_load_env_file_does_not_override_existing_value(tmp_path, monkeypatch):
    monkeypatch.setenv("EXISTING_TEST_KEY", "process-value")
    path = tmp_path / ".env"
    path.write_text("EXISTING_TEST_KEY=file-value\n", encoding="utf-8")

    load_env_file(path)

    assert os.environ["EXISTING_TEST_KEY"] == "process-value"
