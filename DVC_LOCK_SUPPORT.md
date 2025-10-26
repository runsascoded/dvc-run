# `dvc.lock` Support Specification

## Goal

Make `dvc-run` a **drop-in replacement** for `dvc repro` with parallel execution, including full `dvc.lock` management.

## Current State

**dvc-run (v0.1.0):**
- ✅ Parses `dvc.yaml`
- ✅ Builds dependency DAG
- ✅ Executes stages in parallel (respecting dependencies)
- ❌ Does NOT update `dvc.lock`
- ❌ Does NOT check if stages are up-to-date

**Consequence:** Users must run `dvc repro --force` after `dvc-run` to update `dvc.lock`, defeating the speed benefit.

## Target State

**dvc-run (v1.0.0) should:**
- ✅ Parse `dvc.yaml` and `dvc.lock`
- ✅ Build dependency DAG
- ✅ Check stage freshness (compare hashes in `dvc.lock` vs. current files)
- ✅ Skip up-to-date stages (like `dvc repro`)
- ✅ Execute out-of-date stages in parallel
- ✅ Update `dvc.lock` after each stage completes (thread-safe)
- ✅ Be a drop-in replacement for `dvc repro`

## Use Cases

### Use Case 1: Drop-in Replacement
```bash
# Before (serial):
dvc repro                    # 15 minutes

# After (parallel):
dvc-run                      # 4 minutes
# dvc.lock is updated, git diff shows changes
```

### Use Case 2: Incremental Builds
```bash
# User edits one input file
vim njdot/crashes.py

# dvc-run only re-runs affected stages
dvc-run
# Level 1: all cached (hashes match) ✓ skip
# Level 2: combined_crashes out-of-date → run
# Level 3: all out-of-date (depend on crashes) → run
# Level 4: all out-of-date (depend on parquets) → run
```

### Use Case 3: Force Re-run
```bash
# Like dvc repro --force
dvc-run --force
# Ignores dvc.lock, runs everything
```

### Use Case 4: Validation
```bash
# Re-run everything, verify byte-wise reproducibility
dvc-run --validate
# 1. Backup current dvc.lock
# 2. Force re-run all stages
# 3. Compare new hashes vs. backup
# 4. Report any mismatches (non-reproducible stages)
```

## Implementation Requirements

### 1. Parse `dvc.lock`

**Format:**
```yaml
schema: '2.0'
stages:
  stage_name:
    cmd: command that was run
    deps:
      - path: input.txt
        md5: abc123...
        size: 1024
    outs:
      - path: output.txt
        md5: def456...
        size: 2048
```

**Implementation:**
```python
class DVCLockParser:
    def parse(self, lock_path: Path) -> dict[str, StageState]:
        """Parse dvc.lock and return recorded state per stage.

        Returns:
            Dict mapping stage_name → StageState with recorded hashes
        """
        if not lock_path.exists():
            return {}  # No lock file = all stages out-of-date

        lock = yaml.safe_load(lock_path.read_text())
        stages = {}

        for stage_name, stage_data in lock.get('stages', {}).items():
            stages[stage_name] = StageState(
                cmd=stage_data['cmd'],
                deps={d['path']: d['md5'] for d in stage_data.get('deps', [])},
                outs={o['path']: o['md5'] for o in stage_data.get('outs', [])},
            )

        return stages
```

### 2. Check Stage Freshness

**Logic:**
A stage is **up-to-date** if:
1. It exists in `dvc.lock`
2. Its `cmd` hasn't changed
3. All dependency hashes match current file hashes
4. All output hashes match current file hashes

**Implementation:**
```python
def is_stage_fresh(stage: Stage, lock_state: StageState | None) -> bool:
    """Check if stage needs to run.

    Args:
        stage: Stage definition from dvc.yaml
        lock_state: Recorded state from dvc.lock (None if not in lock)

    Returns:
        True if stage is up-to-date and can be skipped
    """
    # No lock state = never run before
    if lock_state is None:
        return False

    # Command changed = must re-run
    if stage.cmd != lock_state.cmd:
        return False

    # Check all dependencies
    for dep_path in stage.deps:
        if not Path(dep_path).exists():
            return False  # Missing dep = can't be fresh

        current_md5 = compute_md5(dep_path)
        recorded_md5 = lock_state.deps.get(dep_path)

        if current_md5 != recorded_md5:
            return False  # Dependency changed

    # Check all outputs
    for out_path in stage.outs:
        if not Path(out_path).exists():
            return False  # Missing output = must re-run

        current_md5 = compute_md5(out_path)
        recorded_md5 = lock_state.outs.get(out_path)

        if current_md5 != recorded_md5:
            return False  # Output changed (or deleted+recreated)

    # All checks passed!
    return True
```

### 3. Thread-Safe `dvc.lock` Updates

**Problem:** Multiple stages running in parallel must update `dvc.lock` without corruption.

**Solution:** File-based locking with atomic updates.

**Implementation:**
```python
from filelock import FileLock
from pathlib import Path
import yaml

class DVCLockWriter:
    """Thread-safe writer for dvc.lock."""

    def __init__(self, lock_path: Path = Path('dvc.lock')):
        self.lock_path = lock_path
        self.file_lock = FileLock(lock_path.with_suffix('.lock'))

    def update_stage(self, stage: Stage, deps_hashes: dict, outs_hashes: dict):
        """Update a single stage in dvc.lock (thread-safe).

        Args:
            stage: Stage that just completed
            deps_hashes: {path: md5} for all dependencies
            outs_hashes: {path: md5} for all outputs
        """
        with self.file_lock:
            # Read current lock state
            if self.lock_path.exists():
                lock = yaml.safe_load(self.lock_path.read_text())
            else:
                lock = {'schema': '2.0', 'stages': {}}

            # Update our stage
            lock['stages'][stage.name] = {
                'cmd': stage.cmd,
                'deps': [
                    {
                        'path': path,
                        'md5': md5,
                        'size': Path(path).stat().st_size,
                    }
                    for path, md5 in deps_hashes.items()
                ],
                'outs': [
                    {
                        'path': path,
                        'md5': md5,
                        'size': Path(path).stat().st_size,
                    }
                    for path, md5 in outs_hashes.items()
                ],
            }

            # Atomic write (write to temp, then rename)
            temp_path = self.lock_path.with_suffix('.tmp')
            temp_path.write_text(yaml.dump(lock, sort_keys=False))
            temp_path.rename(self.lock_path)
```

### 4. MD5 Computation

**Use DVC's hashing logic** to ensure compatibility:

**Implementation:**
```python
import hashlib

def compute_md5(file_path: Path) -> str:
    """Compute MD5 hash of a file (DVC-compatible).

    For files: MD5 of contents
    For directories: MD5 of {relpath: md5} JSON (DVC's .dir format)
    """
    if not file_path.exists():
        raise FileNotFoundError(f"{file_path} not found")

    if file_path.is_file():
        # File: hash contents
        md5 = hashlib.md5()
        with open(file_path, 'rb') as f:
            for chunk in iter(lambda: f.read(65536), b''):
                md5.update(chunk)
        return md5.hexdigest()

    elif file_path.is_dir():
        # Directory: hash of sorted {relpath: md5} mapping
        import json

        file_hashes = {}
        for subfile in sorted(file_path.rglob('*')):
            if subfile.is_file():
                rel_path = subfile.relative_to(file_path)
                file_hashes[str(rel_path)] = compute_md5(subfile)

        # Hash the JSON representation
        json_str = json.dumps(file_hashes, sort_keys=True)
        return hashlib.md5(json_str.encode()).hexdigest()

    else:
        raise ValueError(f"{file_path} is neither file nor directory")
```

### 5. Updated Execution Flow

```python
def run(self, force: bool = False, validate: bool = False):
    """Execute pipeline with dvc.lock support.

    Args:
        force: Re-run all stages (ignore dvc.lock)
        validate: Re-run all + verify reproducibility
    """
    # 1. Parse dvc.yaml
    stages = self.parser.parse()

    # 2. Parse dvc.lock (if not forcing)
    lock_states = {} if force else self.lock_parser.parse()

    # 3. Build DAG
    dag = self.dag_builder.build(stages)
    levels = self.dag_builder.topological_sort()

    # 4. For validation: backup dvc.lock
    if validate:
        backup_lock = self.lock_path.read_text()

    # 5. Execute level-by-level
    lock_writer = DVCLockWriter(self.lock_path)

    for level_idx, level in enumerate(levels, 1):
        print(f"\n=== Level {level_idx}/{len(levels)} ===")

        # Filter out fresh stages (unless forcing)
        stages_to_run = []
        for stage_name in level:
            stage = stages[stage_name]
            lock_state = lock_states.get(stage_name)

            if force or not is_stage_fresh(stage, lock_state):
                stages_to_run.append(stage)
            else:
                print(f"✓ {stage_name} (up-to-date)")

        if not stages_to_run:
            continue  # All cached!

        # Run stages in parallel
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self.run_stage, stage): stage
                for stage in stages_to_run
            }

            for future in as_completed(futures):
                stage = futures[future]
                try:
                    deps_hashes, outs_hashes = future.result()

                    # Update dvc.lock (thread-safe)
                    lock_writer.update_stage(stage, deps_hashes, outs_hashes)

                    print(f"✓ {stage.name} (completed)")
                except Exception as e:
                    print(f"✗ {stage.name} failed: {e}")
                    raise

    # 6. For validation: compare hashes
    if validate:
        self.validate_reproducibility(backup_lock, self.lock_path.read_text())

def run_stage(self, stage: Stage) -> tuple[dict, dict]:
    """Run a single stage and return hashes.

    Returns:
        (deps_hashes, outs_hashes) as {path: md5} dicts
    """
    # Hash dependencies before running
    deps_hashes = {
        dep: compute_md5(Path(dep))
        for dep in stage.deps
    }

    # Execute command
    result = subprocess.run(
        stage.cmd,
        shell=True,
        check=True,
        capture_output=True,
    )

    # Hash outputs after running
    outs_hashes = {
        out: compute_md5(Path(out))
        for out in stage.outs
    }

    return deps_hashes, outs_hashes
```

## Command-Line Interface

### Mirror `dvc repro` options:

```bash
# Basic usage (parallel repro)
dvc-run

# Force re-run all stages
dvc-run --force

# Dry run (show what would execute)
dvc-run --dry-run

# Run specific stages
dvc-run stage1 stage2

# Control parallelism
dvc-run -j 8

# Validation mode (verify reproducibility)
dvc-run --validate

# Don't update dvc.lock (like dvc repro --no-commit)
dvc-run --no-lock-update

# Verbose output
dvc-run -v
```

### Additional options specific to `dvc-run`:

```bash
# Show execution plan with timing estimates
dvc-run --plan

# Profile stages (record execution times)
dvc-run --profile

# Compare against dvc.lock (dry-run freshness check)
dvc-run --status
```

## Compatibility with DVC

**Must be compatible with:**
- ✅ `dvc status` - should recognize `dvc.lock` updates
- ✅ `dvc diff` - should work with our `dvc.lock`
- ✅ `dvc repro` - should be able to run after `dvc-run`
- ✅ Git workflows - `dvc.lock` should diff cleanly
- ✅ User's `~/.rc/dvc` tools - read hashes from `dvc.lock`

**Testing compatibility:**
```bash
# 1. Run dvc-run
dvc-run

# 2. Verify DVC recognizes state
dvc status  # Should show "Data and pipelines are up to date."

# 3. Verify no changes on re-run
dvc repro   # Should skip all stages

# 4. Verify git-diff-dvc.sh works
git add dvc.lock
git commit -m "Update pipeline"
git diff HEAD^..HEAD -- crashes.parquet.dvc  # Should show diff
```

## Migration Path

### Phase 1: Basic `dvc.lock` Support
- Parse `dvc.lock`
- Check stage freshness
- Skip up-to-date stages
- **No updates to `dvc.lock`** (users run `dvc repro --force` after)

### Phase 2: Thread-Safe Updates
- Update `dvc.lock` after each stage completes
- File-based locking
- Atomic writes

### Phase 3: Full Feature Parity
- All `dvc repro` options
- Validation mode
- Profiling
- Error recovery

### Phase 4: Upstream?
- Propose as DVC feature/PR
- Or maintain as parallel execution engine
- Depends on DVC team receptiveness

## Open Questions

### 1. Should we depend on DVC as a library?

**Option A: Pure Python (current approach)**
- ✅ No DVC dependency
- ❌ Reimplements MD5 hashing
- ❌ May diverge from DVC's behavior

**Option B: Import from DVC**
```python
from dvc.repo import Repo
from dvc.hash_info import HashInfo
from dvc.stage import Stage as DVCStage
```
- ✅ Perfect compatibility
- ✅ Reuse battle-tested code
- ❌ Heavy dependency (~100 deps)
- ❌ Couples us to DVC internals

**Recommendation:** Start with Option A, use DVC's public API for hashing if available.

### 2. Should we support DVC's cache?

DVC stores outputs in `.dvc/cache/files/md5/...`. Should `dvc-run`:
- Just compute hashes (like now)?
- Also populate DVC cache (for `dvc push` compatibility)?

**Recommendation:** Just compute hashes for now. Users can run `dvc commit` after if needed.

### 3. What about `foreach` and `matrix` stages?

DVC expands these into multiple stages:
```yaml
stages:
  process:
    foreach: [2020, 2021, 2022, 2023]
    do:
      cmd: process.py ${item}
      deps: [data/${item}.csv]
      outs: [out/${item}.txt]

# Expands to: process@2020, process@2021, process@2022, process@2023
```

Should we:
- Support expansion ourselves?
- Shell out to `dvc` to expand?
- Document as "not yet supported"?

**Recommendation:** Phase 2 feature. Parse expanded stages from `dvc.lock` for now.

### 4. How do we handle the lock file during parallel writes?

Current approach uses `filelock` library:
```python
with FileLock('dvc.lock.lock'):
    # Update dvc.lock
```

**Potential issues:**
- Lock file in `.gitignore`?
- Lock contention = serial bottleneck?
- Better alternatives?

**Alternatives:**
- `fcntl.flock` (Unix only, but faster)
- Per-stage lock files in `dvc.lock.d/`
- Optimistic concurrency (read-modify-write loop)

**Recommendation:** Start with `filelock`, profile lock contention, optimize if needed.

### 5. Should this live in `dvc-run` or be a PR to DVC?

**Pros of separate repo:**
- ✅ Faster iteration
- ✅ No approval process
- ✅ Can experiment freely

**Pros of DVC PR:**
- ✅ Native integration
- ✅ Maintained by DVC team
- ✅ Users get it automatically

**Recommendation:** Prove value in `dvc-run` first, then propose to DVC as enhancement.

## Success Criteria

`dvc-run` v1.0 should:
1. ✅ Be faster than `dvc repro` (3-5x for NJDOT pipeline)
2. ✅ Produce identical `dvc.lock` to `dvc repro`
3. ✅ Skip up-to-date stages correctly
4. ✅ Handle parallel updates without corruption
5. ✅ Be compatible with DVC CLI (`dvc status`, `dvc diff`)
6. ✅ Work with user's existing DVC tools (`git-diff-dvc.sh`)

## Related Links

- [DVC repro documentation](https://dvc.org/doc/command-reference/repro)
- [dvc.lock format](https://dvc.org/doc/user-guide/project-structure/dvcyaml-files)
- [DVC issue #755: Parallel execution](https://github.com/iterative/dvc/issues/755)
- [DVC issue #9805: Parallel repro doesn't work](https://github.com/iterative/dvc/issues/9805)
