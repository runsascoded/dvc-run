# Example Pipeline

This directory contains a simple example pipeline demonstrating `dvc-run`'s parallel execution capabilities.

## Pipeline Structure

```
Level 1 (parallel):     data_a    data_b    data_c
                          |  \      /         |
Level 2 (parallel):       |   merge_ab    process_c
                          |      |            |
Level 3:                  +------+------------+
                                  |
                               final
```

## Running the Example

```bash
# Show execution plan without running
dvc-run --dry-run

# Run with default parallelism (CPU count)
dvc-run

# Run with 4 parallel jobs
dvc-run -j 4

# Run again - should skip all stages (up-to-date)
dvc-run
```

## Expected Output

First run:
```
Execution plan (3 levels, 6 stages):
  Level 1: data_a, data_b, data_c
  Level 2: merge_ab, process_c
  Level 3: final

Level 1/3: 3 stage(s)
  ⟳ data_a: running...
  ⟳ data_b: running...
  ⟳ data_c: running...
  ✓ data_a: completed
  ✓ data_b: completed
  ✓ data_c: completed
Level 2/3: 2 stage(s)
  ⟳ merge_ab: running...
  ⟳ process_c: running...
  ✓ merge_ab: completed
  ✓ process_c: completed
Level 3/3: 1 stage(s)
  ⟳ final: running...
  ✓ final: completed

Summary:
  Total stages: 6
  Executed: 6
  Skipped (up-to-date): 0
```

Second run (everything up-to-date):
```
Execution plan (3 levels, 6 stages):
  Level 1: data_a, data_b, data_c
  Level 2: merge_ab, process_c
  Level 3: final

Level 1/3: 3 stage(s)
  ⊙ data_a: Data and pipelines are up to date.
  ⊙ data_b: Data and pipelines are up to date.
  ⊙ data_c: Data and pipelines are up to date.
Level 2/3: 2 stage(s)
  ⊙ merge_ab: Data and pipelines are up to date.
  ⊙ process_c: Data and pipelines are up to date.
Level 3/3: 1 stage(s)
  ⊙ final: Data and pipelines are up to date.

Summary:
  Total stages: 6
  Executed: 0
  Skipped (up-to-date): 6
```

## Key Features Demonstrated

1. **Parallel execution**: Level 1 runs `data_a`, `data_b`, and `data_c` simultaneously
2. **Dependency tracking**: Level 2 waits for Level 1 to complete
3. **Incremental builds**: Re-running skips up-to-date stages
4. **Hash-based freshness**: Uses DVC's hash checking via `dvc status`
