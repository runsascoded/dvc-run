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

import sys
from pathlib import Path

import click

from dvc_run.dag import DAG
from dvc_run.executor import ParallelExecutor
from dvc_run.parser import DVCYamlParser


@click.command()
@click.argument('stages', nargs=-1)
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
@click.option(
    '--validate',
    is_flag=True,
    help='Validate reproducibility by re-running and comparing hashes',
)
@click.option(
    '--dot',
    'dot_output',
    type=click.Path(path_type=Path),
    help='Export DAG as GraphViz DOT format to file',
)
@click.option(
    '--svg',
    'svg_output',
    type=click.Path(path_type=Path),
    help='Export DAG as SVG to file (requires graphviz)',
)
@click.option(
    '--mermaid',
    'mermaid_output',
    type=click.Path(path_type=Path),
    help='Export DAG as Mermaid diagram to file',
)
def main(
    stages: tuple[str, ...],
    dry_run: bool,
    jobs: int | None,
    dvc_yaml: Path,
    verbose: bool,
    validate: bool,
    dot_output: Path | None,
    svg_output: Path | None,
    mermaid_output: Path | None,
):
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
        all_stages = parser.parse()

        if not all_stages:
            click.echo("No stages found in dvc.yaml", err=True)
            sys.exit(1)

        if verbose:
            click.echo(f"Found {len(all_stages)} stage(s)", err=True)

        # Build DAG
        dag = DAG(all_stages)

        # Filter to selected stages if specified
        if stages:
            try:
                dag = dag.filter_to_targets(list(stages))
                if verbose:
                    click.echo(
                        f"Filtered to {len(dag.stages)} stage(s) "
                        f"(targets + dependencies)",
                        err=True,
                    )
            except ValueError as e:
                click.echo(f"Error: {e}", err=True)
                sys.exit(1)

        # Check for cycles
        cycle = dag.check_cycles()
        if cycle:
            click.echo(
                f"Error: Circular dependency detected: {' -> '.join(cycle)}",
                err=True,
            )
            sys.exit(1)

        # Export visualizations if requested
        if dot_output or svg_output or mermaid_output:
            from dvc_run.viz import DAGVisualizer

            viz = DAGVisualizer(dag)

            if dot_output:
                viz.to_dot_file(dot_output)
                click.echo(f"Exported DOT to {dot_output}", err=True)

            if svg_output:
                try:
                    viz.to_svg(svg_output)
                    click.echo(f"Exported SVG to {svg_output}", err=True)
                except RuntimeError as e:
                    click.echo(f"Error: {e}", err=True)
                    sys.exit(1)

            if mermaid_output:
                mermaid_output.write_text(viz.to_mermaid())
                click.echo(f"Exported Mermaid to {mermaid_output}", err=True)

            # If only exporting visualizations (no execution), exit
            if dry_run or (dot_output or svg_output or mermaid_output):
                return

        # Validation mode: backup lock, force re-run, compare
        backup_lock_content = None
        lock_path = Path("dvc.lock")

        if validate:
            if lock_path.exists():
                backup_lock_content = lock_path.read_text()
                click.echo("Validation mode: Backing up dvc.lock...", err=True)
            else:
                click.echo("Warning: No dvc.lock exists, cannot validate", err=True)
                validate = False

        # Execute
        executor = ParallelExecutor(
            dag=dag,
            max_workers=jobs,
            dry_run=dry_run,
            output=sys.stderr,
            force=validate,  # Force re-run in validate mode
        )

        results = executor.execute()

        # Validation: compare hashes
        if validate and backup_lock_content:
            click.echo("\nValidating reproducibility...", err=True)

            import yaml
            backup_lock = yaml.safe_load(backup_lock_content)
            new_lock = yaml.safe_load(lock_path.read_text())

            mismatches = []
            for stage_name in dag.stages.keys():
                if stage_name not in backup_lock.get('stages', {}):
                    continue
                if stage_name not in new_lock.get('stages', {}):
                    continue

                old_stage = backup_lock['stages'][stage_name]
                new_stage = new_lock['stages'][stage_name]

                # Compare output hashes
                for old_out, new_out in zip(
                    old_stage.get('outs', []),
                    new_stage.get('outs', [])
                ):
                    if old_out.get('md5') != new_out.get('md5'):
                        mismatches.append({
                            'stage': stage_name,
                            'file': old_out['path'],
                            'old_md5': old_out.get('md5'),
                            'new_md5': new_out.get('md5'),
                        })

            if mismatches:
                click.echo("\n⚠️  Non-reproducible stages detected:", err=True)
                for m in mismatches:
                    click.echo(
                        f"  {m['stage']}: {m['file']} "
                        f"({m['old_md5'][:8]}... → {m['new_md5'][:8]}...)",
                        err=True,
                    )
                sys.exit(1)
            else:
                click.echo("✓ All stages are reproducible!", err=True)

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
