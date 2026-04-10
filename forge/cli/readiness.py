"""Forge CLI readiness command. Shows provider status, routing, and pipeline readiness."""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.table import Table


@click.command("readiness")
def readiness() -> None:
    """Check provider connections, stage routing, and pipeline readiness."""
    from forge.config.project_config import ProjectConfig, apply_project_config
    from forge.config.settings import ForgeSettings
    from forge.config.user_settings import load_local_user_settings
    from forge.core.provider_config import (
        apply_user_settings,
        build_provider_registry,
        ensure_routing_defaults,
        normalize_routing_settings,
    )
    from forge.providers.readiness import build_readiness_report
    from forge.providers.status import (
        collect_provider_connection_statuses,
        preferred_default_provider,
    )

    project_config = ProjectConfig.load(".")
    settings = ForgeSettings()
    apply_project_config(settings, project_config)
    apply_user_settings(settings, load_local_user_settings())
    preferred_provider = preferred_default_provider(collect_provider_connection_statuses())
    ensure_routing_defaults(settings, preferred_provider)
    registry = build_provider_registry(settings, project_config)
    normalize_routing_settings(settings, registry, preferred_provider=preferred_provider)

    report = build_readiness_report(settings, registry)

    console = Console()
    console.print()

    # -- Section 1: Providers --------------------------------------------------
    console.print("[bold]Providers[/bold]\n")
    prov_table = Table(show_header=True, header_style="bold", pad_edge=False)
    prov_table.add_column("Provider")
    prov_table.add_column("Installed")
    prov_table.add_column("Connected")
    prov_table.add_column("Auth Source")
    prov_table.add_column("Status")

    for p in report.providers:
        installed = "[green]Yes[/green]" if p.installed else "[red]No[/red]"
        connected = "[green]Yes[/green]" if p.connected else "[red]No[/red]"
        auth = p.auth_source or "-"
        prov_table.add_row(p.display_name, installed, connected, auth, p.status)

    console.print(prov_table)
    console.print()

    # -- Section 2: Stage Routing ----------------------------------------------
    console.print("[bold]Stage Routing[/bold]\n")
    route_table = Table(show_header=True, header_style="bold", pad_edge=False)
    route_table.add_column("Stage")
    route_table.add_column("Provider")
    route_table.add_column("Model")
    route_table.add_column("Backend")
    route_table.add_column("Effort")
    route_table.add_column("Warnings")

    for r in report.routing:
        effort = r.reasoning_effort or "auto"
        warnings = ", ".join(r.warnings) if r.warnings else "-"
        route_table.add_row(r.label, r.provider, r.model, r.backend, effort, warnings)

    console.print(route_table)
    console.print()

    # -- Section 3: Readiness --------------------------------------------------
    if report.ready:
        console.print("[green bold]Ready to run pipelines[/green bold]")
    else:
        console.print("[red bold]Blocking issues:[/red bold]")
        for issue in report.blocking_issues:
            console.print(f"  [red]- {issue}[/red]")

    if report.warnings:
        console.print()
        console.print("[yellow bold]Warnings:[/yellow bold]")
        for w in report.warnings:
            console.print(f"  [yellow]- {w}[/yellow]")

    console.print()
    sys.exit(0 if report.ready else 1)
