# dvc-run

[![Tests](https://github.com/runsascoded/dvc-run/actions/workflows/test.yml/badge.svg)](https://github.com/runsascoded/dvc-run/actions/workflows/test.yml)
[![Lint](https://github.com/runsascoded/dvc-run/actions/workflows/lint.yml/badge.svg)](https://github.com/runsascoded/dvc-run/actions/workflows/lint.yml)

**Local parallel execution engine for DVC pipelines**

## The Problem

[DVC (Data Version Control)][dvc] provides excellent content-addressable storage and versioning for data pipelines:
- `.dvc` files track outputs with MD5 hashes
- `dvc.yaml` defines pipeline stages with inputs/outputs/commands
- `dvc.lock` records actual hashes after execution
- Hash-based change detection avoids unnecessary re-runs

However, **DVC's execution model is fundamentally serial**:
- `dvc repro` runs one stage at a time
- No built-in parallel execution of independent stages
- Manual parallelization (`dvc repro stage1 & dvc repro stage2 &`) is fragile
- `foreach`/`matrix` expand to multiple stages, but still run serially

For local development and iteration, this is a significant bottleneck. Production solutions (Airflow, GitHub Actions + CML, Kubernetes) are heavyweight and designed for CI/CD, not local workflows.

## The Gap

DVC is **95% of what we need** for hash-based incremental builds:
1. ✅ Content-addressable storage with hash tracking
2. ✅ Dependency graphs via `dvc.yaml`
3. ✅ Up-to-date checks via `dvc status`
4. ✅ Storage backends (S3, GCS, Azure, etc.)
5. ❌ **Local parallel execution**

The missing 5% is a lightweight orchestration layer that:
- Respects DVC's dependency DAG
- Runs independent stages in parallel
- Leverages DVC's existing hash checks
- Doesn't require heavyweight infrastructure

## The Solution: `dvc-run`

A simple CLI tool that wraps DVC with parallel execution:

```bash
# Instead of:
dvc repro  # runs serially

# Use:
dvc-run    # runs independent stages in parallel
dvc-run -j 8  # control parallelism
dvc-run --dry-run  # show execution plan
```

### Key Design Principles

1. **DVC as the source of truth**: Read `dvc.yaml` for stage definitions, use `dvc status` for freshness checks, call `dvc repro` for execution

2. **Parallel by default**: Automatically detect independent stages and run them concurrently

3. **Zero config**: Works with existing DVC repos, no additional files needed

4. **Explicit over implicit**: Clear execution plan, verbose logging, fail-fast behavior

5. **Local-first**: Optimized for developer workflows, not production orchestration

## Installation

```bash
# Clone the repository
git clone https://github.com/runsascoded/dvc-run
cd dvc-run

# Install with uv (recommended)
uv sync
uv pip install -e .

# Or with pip
pip install -e .
```

## Quick Start

```bash
# Navigate to your DVC project
cd /path/to/your/dvc/project

# Show execution plan
dvc-run --dry-run

# Run pipeline with parallel execution
dvc-run

# Limit parallelism
dvc-run -j 4

# Run specific stages (and their dependencies)
dvc-run stage1 stage2

# Validate reproducibility
dvc-run --validate
```

See [EXAMPLES.md](./EXAMPLES.md) for demo pipelines including:
- [example-basic/](./example-basic) - Simple pipeline with dependency tracking
- [example-parallel-heavy/](./example-parallel-heavy) - Many parallel stages (10x speedup)

## Architecture

### Stage Execution Model

```
Parse dvc.yaml → Build DAG → Topological sort → Execute by levels
                                                   ↓
                                          Level 1: [A, B, C] ← parallel
                                          Level 2: [D, E]    ← parallel (waits for Level 1)
                                          Level 3: [F]       ← serial (waits for Level 2)
```

Within each level, stages run in parallel (up to `-j` workers).

### Freshness Checks

Use DVC's built-in logic:
```python
# Option 1: Shell out (simple, robust)
result = subprocess.run(["dvc", "status", stage_name])
is_fresh = (result.returncode == 0 and not result.stdout.strip())

# Option 2: Parse dvc.lock (faster, more complex)
# Compare current file hashes against dvc.lock
```

### Dependency Resolution

Parse `dvc.yaml` to extract:
- Stage names
- Commands
- Dependencies (`deps`)
- Outputs (`outs`)

Build directed acyclic graph (DAG):
- Nodes = stages
- Edges = dependencies (file or stage)

Detect circular dependencies and fail early.

### Parallel Execution

Use Python's `concurrent.futures.ThreadPoolExecutor`:
- Thread pool size = `-j` parameter (default: CPU count)
- Execute stages level-by-level
- Wait for entire level to complete before next level
- Fail-fast: if any stage fails, cancel remaining work

## Use Cases

### 1. Data Pipeline Iteration

**Before (serial):**
```bash
$ time dvc repro
# Stage 1: raw_data (60s)
# Stage 2: clean_data (60s) ← waits for Stage 1
# Total: 120s
```

**After (parallel):**
```bash
$ time dvc-run -j 4
# Level 1: [raw_data_2020, raw_data_2021, raw_data_2022, raw_data_2023] ← parallel (60s)
# Level 2: [clean_data] (60s) ← waits for Level 1
# Total: 120s → 60s
```

### 2. Database Generation

**Scenario:** Generate SQLite databases from Parquet files

```yaml
# dvc.yaml
stages:
  crashes_db:
    cmd: parquet2db crashes.parquet crashes.db
    deps: [crashes.parquet]
    outs: [crashes.db]

  vehicles_db:
    cmd: parquet2db vehicles.parquet vehicles.db
    deps: [vehicles.parquet]
    outs: [vehicles.db]

  drivers_db:
    cmd: parquet2db drivers.parquet drivers.db
    deps: [drivers.parquet]
    outs: [drivers.db]
```

**With dvc-run:**
- All three DB builds run in parallel
- Byte-wise reproducible (DVC tracks hashes)
- Only rebuilds if `.parquet` changed

### 3. Multi-Year Processing

**Scenario:** Process crash data for years 2001-2023

```yaml
# dvc.yaml with foreach
stages:
  process_year:
    foreach: [2001, 2002, ..., 2023]
    do:
      cmd: process_crashes.py ${item}
      deps: [raw/${item}.zip]
      outs: [processed/${item}.parquet]
```

**With DVC:** Runs 23 stages serially

**With dvc-run:** Runs 23 stages in parallel (up to `-j` limit)

## Non-Goals

- **Production orchestration**: Use Airflow/Prefect/Dagster for that
- **Remote execution**: Use CML + GitHub Actions for that
- **Workflow DSL**: Use DVC's existing `dvc.yaml` format
- **State management**: Let DVC handle `dvc.lock` and `.dvc` files
- **Data versioning**: DVC already does this perfectly

## Similar Tools

| Tool | Hash-based | Parallel | Local-first | DVC Integration |
|------|-----------|----------|-------------|-----------------|
| DVC Pipelines | ✅ | ❌ | ✅ | Native |
| doit | ✅ | ✅ | ✅ | Manual |
| Snakemake | ❌ (timestamps) | ✅ | ✅ | Conflicts |
| Airflow | ❌ | ✅ | ❌ | Integration layer |
| **dvc-run** | ✅ (via DVC) | ✅ | ✅ | Native |

## Open Questions

1. **Validation mode**: Should we have a `--validate` flag that re-runs entire pipeline and checks byte-wise reproducibility?

2. **DVC lock file**: Should we respect DVC's lockfile during parallel execution? (Risk of corruption if multiple `dvc repro` run concurrently)

3. **Progress reporting**: How to display parallel stage progress without overwhelming output?

4. **Error handling**: If stage fails, should we:
   - Cancel all remaining work immediately?
   - Let current level finish?
   - Continue with independent stages?

5. **Stage selection**: Support DVC's stage targeting (`dvc repro stage1 stage2`)?

6. **Caching strategy**: Cache `dvc status` results within execution? Files don't change during run.

7. **Integration with dvc.lock**: Parse ourselves vs. shell out to `dvc status`?

## Implementation Plan

### Phase 1: MVP
- Parse `dvc.yaml` (simple stages only, no `foreach`/`matrix`)
- Build DAG from dependencies
- Topological sort into levels
- Parallel execution using ThreadPoolExecutor
- Use `dvc status` for freshness checks
- Use `dvc repro <stage>` for execution

### Phase 2: Features
- Support `foreach` and `matrix` stages
- Add `--dry-run` for execution plan
- Add `--validate` for reproducibility checking
- Better progress reporting
- Stage selection/filtering

### Phase 3: Optimizations
- Parse `dvc.lock` directly (avoid subprocess overhead)
- Smarter caching of status checks
- Process pool option (vs. thread pool)
- Integration tests with real DVC repos

## Prior Art

- [doit](https://pydoit.org/) - Hash-based task runner (but not DVC-aware)
- [pytask](https://github.com/pytask-dev/pytask) - Reproducible workflows (but own DSL)
- [DVC experiments](https://dvc.org/doc/user-guide/experiment-management) - Parallel experiment queue (but different use case)
- Manual parallelization: `dvc repro stage1 & dvc repro stage2 &` (fragile, no DAG awareness)

## License

MIT (or similar permissive license)

## Links

- [DVC Documentation][dvc]
- [DVC Pipelines Guide](https://dvc.org/doc/user-guide/pipelines)
- [DVC Issue #647: Pipeline parallel steps](https://github.com/iterative/dvc/issues/647)

[dvc]: https://dvc.org/
