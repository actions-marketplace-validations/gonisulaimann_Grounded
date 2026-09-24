import typer

cli = typer.Typer()


@cli.command()
def hello(name: str, dry_run: bool = False):
    print(name)
