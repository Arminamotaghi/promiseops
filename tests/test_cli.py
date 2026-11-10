import pytest

from promiseops.cli import main


def test_score_priority_accepts_timezone_aware_as_of(monkeypatch, capsys):
    monkeypatch.setattr(
        "sys.argv",
        ["promiseops", "score-priority", "--as-of", "2018-01-01T00:00:00Z"],
    )

    assert main() == 1

    output = capsys.readouterr()
    assert "database and model integration are not implemented" in output.err
    assert "2018-01-01T00:00:00+00:00" in output.err


def test_score_priority_rejects_as_of_without_timezone(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["promiseops", "score-priority", "--as-of", "2018-01-01"],
    )

    with pytest.raises(SystemExit) as exc:
        main()

    assert exc.value.code == 2
