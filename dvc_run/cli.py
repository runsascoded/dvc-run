#!/usr/bin/env -S uv run
"""CLI for dvc-run parallel execution engine.

/// script
requires-python = ">=3.10"
dependencies = [
    "click>=8.0",
    "pyyaml>=6.0",
]
///
"""

import os
import sys
from pathlib import Path

import click

from dvc_run.dag import DAG
from dvc_run.executor import ParallelExecutor
from dvc_run.parser import DVCYamlParser


@click.command()
@click.option(
    '-d',
    '--dry-run',
    is_flag=True,
    help='Show execution plan without running stages',
)
@click.option(
    '-j',
    '--jobs',
    type=int,
    default=None,
    help='Number of parallel jobs (default: CPU count)',
)
@click.option(
    '-f',
    '--file',
    'dvc_yaml',
    type=click.Path(exists=True, path_type=Path),
    default='dvc.yaml',
    help='Path to dvc.yaml file',
)
@click.option(
    '-v',
    '--verbose',
    is_flag=True,
    help='Enable verbose output',
)
def main(dry_run: bool, jobs: int | None, dvc_yaml: Path, verbose: bool):
    """Execute DVC pipeline stages in parallel.

    dvc-run reads your dvc.yaml file, builds a dependency graph, and executes
    independent stages in parallel. This can significantly speed up pipeline
    execution compared to serial 'dvc repro'.

    Examples:

        \b
        # Run pipeline with default parallelism
        dvc-run

        \b
        # Limit to 4 parallel jobs
        dvc-run -j 4

        \b
        # Show execution plan without running
        dvc-run --dry-run
    """
    try:
        # Parse dvc.yaml
        if verbose:
            click.echo(f"Parsing {dvc_yaml}...", err=True)

        parser = DVCYamlParser(dvc_yaml)
        stages = parser.parse()

        if not stages:
            click.echo("No stages found in dvc.yaml", err=True)
            sys.exit(1)

        if verbose:
            click.echo(f"Found {len(stages)} stage(s)", err=True)

        # Build DAG
        dag = DAG(stages)

        # Check for cycles
        cycle = dag.check_cycles()
        if cycle:
            click.echo(
                f"Error: Circular dependency detected: {' -> '.join(cycle)}",
                err=True,
            )
            sys.exit(1)

        # Execute
        executor = ParallelExecutor(
            dag=dag,
            max_workers=jobs,
            dry_run=dry_run,
            output=sys.stderr,
        )

        results = executor.execute()

        if not dry_run:
            # Print summary
            total = len(results)
            succeeded = sum(1 for r in results if r.success and not r.skipped)
            skipped = sum(1 for r in results if r.skipped)
            failed = sum(1 for r in results if not r.success)

            click.echo("\nSummary:", err=True)
            click.echo(f"  Total stages: {total}", err=True)
            click.echo(f"  Executed: {succeeded}", err=True)
            click.echo(f"  Skipped (up-to-date): {skipped}", err=True)
            if failed:
                click.echo(f"  Failed: {failed}", err=True)

            if failed > 0:
                sys.exit(1)

    except FileNotFoundError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except ValueError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except RuntimeError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        click.echo("\nInterrupted", err=True)
        sys.exit(130)


if __name__ == '__main__':
    main()
