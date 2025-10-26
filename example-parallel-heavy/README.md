# Parallel-Heavy Example

Demonstrates the performance benefits of parallel execution with many independent stages.

## Pipeline Structure

```
Level 1 (10 parallel):  process_01 ... process_10
Level 2:                merge_all
Level 3:                report
```

## Performance Comparison

**Serial execution (`dvc repro`):**
- 10 stages × 0.5s each = ~5 seconds
- Plus merge and report = ~5.5 seconds total

**Parallel execution (`dvc-run -j 10`):**
- Level 1: 0.5 seconds (all 10 run simultaneously)
- Level 2: merge (instant)
- Level 3: report (instant)
- Total: ~0.5 seconds

**10x speedup** from parallelization!

## Try It

```bash
cd example-parallel-heavy
dvc init
time dvc-run -j 10
```
