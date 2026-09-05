import click

from . import __version__


@click.group()
@click.version_option(__version__, prog_name="sh41 local")
def main() -> None:
    """Run persistent coding agents locally."""
