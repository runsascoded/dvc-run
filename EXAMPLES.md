# Examples

This directory contains example DVC pipelines demonstrating various `dvc-run` features.

## Available Examples

### [example-basic/](./example-basic)
Simple 3-level pipeline demonstrating basic parallel execution and dependency tracking.
- 6 stages across 3 execution levels
- Diamond dependency pattern
- Good starting point for understanding `dvc-run`

### [example-parallel-heavy/](./example-parallel-heavy)
Performance-focused example with many independent stages.
- 10 parallel stages in first level
- Demonstrates ~10x speedup from parallelization
- Useful for benchmarking

## Running Examples

Each example directory contains:
- `dvc.yaml` - Pipeline definition
- `README.md` - Example-specific documentation
- `.gitignore` - Excludes DVC artifacts

To try an example:

```bash
cd example-basic
dvc init
dvc-run --dry-run  # See execution plan
dvc-run            # Run pipeline
```

## Visualizing Pipelines

Export pipeline structure to various formats:

```bash
# DOT format (GraphViz)
dvc-run --dot pipeline.dot
dot -Tpng pipeline.dot -o pipeline.png

# SVG (requires graphviz)
dvc-run --svg pipeline.svg

# Mermaid (for GitHub/docs)
dvc-run --mermaid pipeline.mmd
```
