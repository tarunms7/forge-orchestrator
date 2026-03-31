"""Forge CLI gauntlet command. Run end-to-end pipeline scenario tests."""

from __future__ import annotations

import asyncio

import click


@click.command("gauntlet")
@click.option("--scenario", "-s", multiple=True, help="Run specific scenario(s) by name")
@click.option(
    "--chaos", is_flag=True, default=False, help="Enable chaos mode for failure injection"
)
@click.option(
    "--live", is_flag=True, default=False, help="Run against real Claude SDK (expensive, slow)"
)
@click.option("--format", "fmt", type=click.Choice(["rich", "json"]), default="rich")
@click.option("--output", "-o", type=click.Path(), default=None, help="Write JSON report to file")
@click.option("--verbose", "-v", is_flag=True, default=False)
@click.pass_context
def gauntlet(
    ctx: click.Context,
    scenario: tuple[str, ...],
    chaos: bool,
    live: bool,
    fmt: str,
    output: str | None,
    verbose: bool,
) -> None:
    """Run the Forge gauntlet — end-to-end pipeline scenario tests."""
    from forge.gauntlet.report import format_report_json, format_report_rich, format_report_summary
    from forge.gauntlet.runner import GauntletRunner

    if live and not chaos:
        click.echo(
            "Warning: --live mode uses real Claude SDK calls and costs real money.", err=True
        )

    runner = GauntletRunner(
        scenarios=list(scenario) if scenario else None,
        chaos=chaos,
        live=live,
    )

    try:
        result = asyncio.run(runner.run())
    except KeyboardInterrupt:
        click.echo("\nGauntlet interrupted by user.")
        raise SystemExit(1)
    except Exception as e:
        click.echo(f"Gauntlet failed: {e}", err=True)
        if ctx.obj and ctx.obj.get("verbose"):
            import traceback

            traceback.print_exc()
        raise SystemExit(1)

    if fmt == "rich":
        format_report_rich(result, verbose=verbose)
    else:
        click.echo(format_report_json(result))

    if output:
        json_str = format_report_json(result)
        with open(output, "w", encoding="utf-8") as f:
            f.write(json_str)
        click.echo(f"Report written to {output}")

    # Print one-line summary for CI
    click.echo(format_report_summary(result))

    if not result.passed:
        raise SystemExit(1)
