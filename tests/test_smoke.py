from trace_booth import __version__
from trace_booth.__main__ import main


def test_version():
    assert __version__ == "0.1.0"


def test_main_prints_version(capsys):
    assert main() == 0
    assert "0.1.0" in capsys.readouterr().out
