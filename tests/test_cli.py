"""Smoke tests for the project skeleton."""

from typer.testing import CliRunner

from photo_organizer import __version__
from photo_organizer.cli import app

runner = CliRunner()


def test_cli_prints_banner() -> None:
    result = runner.invoke(app)
    assert result.exit_code == 0
    assert "Photo Organizer" in result.output


def test_package_has_version() -> None:
    assert __version__ == "0.1.0"
