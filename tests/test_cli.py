from click.testing import CliRunner

from sh41_local.cli import main


def test_help_identifies_local_cli():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "locally" in result.output
