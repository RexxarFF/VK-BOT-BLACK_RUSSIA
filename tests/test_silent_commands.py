from pathlib import Path


def test_unknown_commands_are_silent_in_source():
    src = (Path(__file__).parents[1] / "app" / "bot.py").read_text(encoding="utf-8")
    assert "if command not in KNOWN_COMMANDS" in src
    # User-facing legacy phrases must never exist in executable bot source.
    executable = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert "Не понял команду" not in executable
    assert "Неизвестная команда" not in executable
