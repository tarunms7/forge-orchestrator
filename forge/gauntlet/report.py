"""Report formatters for Forge gauntlet results."""

from __future__ import annotations

from forge.gauntlet.models import GauntletResult


def format_report_rich(result: GauntletResult, verbose: bool = False) -> None:
    """Print a Rich console report with panels, tables, and icons."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    console.print()
    console.print(Panel("[bold cyan]FORGE GAUNTLET REPORT[/]", expand=False))
    console.print()

    total_cost = 0.0
    for scenario in result.scenarios:
        icon = "[green]\u2713[/green]" if scenario.passed else "[red]\u2717[/red]"
        status = "[green]PASS[/green]" if scenario.passed else "[red]FAIL[/red]"
        cost_str = f"${scenario.cost_usd:.4f}" if scenario.cost_usd else ""

        console.print(f"  {icon} [bold]{scenario.name}[/]  {status}  [dim]{scenario.duration_s:.1f}s[/]  [dim]{cost_str}[/]")

        if scenario.error:
            console.print(f"    [red]Error: {scenario.error.splitlines()[0]}[/]")

        if verbose and scenario.stages:
            stage_table = Table(show_header=True, header_style="bold", box=None, pad_edge=False, padding=(0, 1, 0, 3))
            stage_table.add_column("Stage", min_width=12)
            stage_table.add_column("Status", width=6, justify="center")
            stage_table.add_column("Duration", width=8, justify="right")
            stage_table.add_column("Details")

            for stage in scenario.stages:
                s_icon = "[green]\u2713[/green]" if stage.passed else "[red]\u2717[/red]"
                stage_table.add_row(
                    stage.name,
                    s_icon,
                    f"{stage.duration_s:.2f}s",
                    stage.details or "",
                )

            console.print(stage_table)

        if verbose and scenario.assertions:
            for assertion in scenario.assertions:
                a_icon = "[green]\u2713[/green]" if assertion.passed else "[red]\u2717[/red]"
                console.print(f"    {a_icon} {assertion.name}: {assertion.message}")

        if scenario.artifacts:
            for art_name, art_path in scenario.artifacts.items():
                console.print(f"    [dim]Artifact:[/] {art_name} = {art_path}")

        total_cost += scenario.cost_usd
        console.print()

    # Footer
    passed_count = sum(1 for s in result.scenarios if s.passed)
    failed_count = len(result.scenarios) - passed_count

    footer_parts = [
        f"[bold]{len(result.scenarios)} scenarios[/]",
        f"[green]{passed_count} passed[/]",
    ]
    if failed_count:
        footer_parts.append(f"[red]{failed_count} failed[/]")
    footer_parts.append(f"[dim]{result.total_duration_s:.1f}s[/]")
    if total_cost > 0:
        footer_parts.append(f"[dim]${total_cost:.4f}[/]")

    console.print("  " + " \u00b7 ".join(footer_parts))
    console.print()


def format_report_json(result: GauntletResult) -> str:
    """Return JSON string of the gauntlet result."""
    return result.model_dump_json(indent=2)


def format_report_summary(result: GauntletResult) -> str:
    """Return a one-line CI summary string."""
    passed_count = sum(1 for s in result.scenarios if s.passed)
    total = len(result.scenarios)
    return f"Gauntlet: {passed_count}/{total} passed in {result.total_duration_s:.1f}s"
