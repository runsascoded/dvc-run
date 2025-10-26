"""DAG visualization utilities."""

from pathlib import Path

from dvc_run.dag import DAG


class DAGVisualizer:
    """Generate visualizations of the dependency graph."""

    def __init__(self, dag: DAG):
        self.dag = dag

    def to_dot(self) -> str:
        """Generate GraphViz DOT format representation.

        Returns:
            String containing DOT format graph
        """
        lines = [
            "digraph pipeline {",
            "  rankdir=LR;",
            "  node [shape=box, style=rounded];",
            "",
        ]

        # Add nodes
        for stage_name in sorted(self.dag.stages.keys()):
            lines.append(f'  "{stage_name}";')

        lines.append("")

        # Add edges
        for stage_name in sorted(self.dag.stages.keys()):
            deps = sorted(self.dag.get_dependencies(stage_name))
            for dep in deps:
                lines.append(f'  "{dep}" -> "{stage_name}";')

        lines.append("}")
        return "\n".join(lines)

    def to_dot_file(self, output_path: Path):
        """Write DOT format to file.

        Args:
            output_path: Path to write DOT file
        """
        output_path.write_text(self.to_dot())

    def to_svg(self, output_path: Path):
        """Generate SVG visualization using GraphViz.

        Args:
            output_path: Path to write SVG file

        Raises:
            RuntimeError: If GraphViz (dot command) is not installed
        """
        import subprocess
        import tempfile

        # Write DOT to temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.dot', delete=False) as f:
            f.write(self.to_dot())
            dot_path = Path(f.name)

        try:
            # Run dot command to generate SVG
            subprocess.run(
                ["dot", "-Tsvg", str(dot_path), "-o", str(output_path)],
                capture_output=True,
                text=True,
                check=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "GraphViz 'dot' command not found. "
                "Install with: brew install graphviz (macOS) or apt-get install graphviz (Linux)"
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Failed to generate SVG: {e.stderr}")
        finally:
            dot_path.unlink()

    def to_mermaid(self) -> str:
        """Generate Mermaid diagram format.

        Returns:
            String containing Mermaid format graph
        """
        lines = ["graph LR"]

        # Add edges (nodes will be created implicitly)
        for stage_name in sorted(self.dag.stages.keys()):
            deps = sorted(self.dag.get_dependencies(stage_name))
            if deps:
                for dep in deps:
                    lines.append(f"  {dep} --> {stage_name}")
            else:
                # Standalone node
                lines.append(f"  {stage_name}")

        return "\n".join(lines)

    def print_levels(self):
        """Print execution levels in text format."""
        levels = self.dag.topological_sort()

        print(f"Execution plan ({len(levels)} levels, {len(self.dag.stages)} stages):")
        for i, level in enumerate(levels, 1):
            stages_str = ", ".join(sorted(level))
            print(f"  Level {i}: {stages_str}")
