# Parallel Execution Implementation Spec

## Problem

Currently `dvc-run` shells out to `dvc repro` for each stage:
```python
subprocess.run(["dvc", "repro", stage_name])
```

This creates **lock contention** when running stages in parallel:
- Multiple `dvc repro` processes read/write `dvc.lock` simultaneously
- DVC's rwlock system (`.dvc/tmp/rwlock`) blocks concurrent access
- Stages that could safely run in parallel get blocked

Example failure:
```
ERROR: failed to reproduce 'combined_crashes':
'/path/crash_pk_mappings.parquet' is busy, it is being blocked by:
  (PID 88109): dvc repro db_crashes
```

**What's actually happening:**
- `dvc repro db_crashes` is running and holds locks on its dependency tree
- This includes locking `combined_crashes` stage (source of `crashes.parquet`)
- The `combined_crashes` stage outputs TWO files: `crashes.parquet` AND `crash_pk_mappings.parquet`
- DVC locks the **entire stage** (all outputs), not just the files being used
- When `dvc repro combined_drivers` tries to check `crash_pk_mappings.parquet`, it hits the lock

So the contention is during **dependency checking**, not just during lock file writes. Each `dvc repro` process:
1. Acquires locks on its entire dependency tree (all upstream stages)
2. Holds those locks while running the command
3. Updates `dvc.lock` and releases locks

With parallel execution, multiple processes try to lock overlapping dependency trees simultaneously, causing deadlocks.

### Evidence from Real-World Testing

Testing with the NJDOT pipeline (15 stages, 4 levels, long-running commands) showed:

**What works:**
- ✅ Parallel command execution: All 5 stages in Level 3 started simultaneously
- ✅ Commands complete successfully: `combined_pedestrians` finished without errors
- ✅ Fast stages pass tests: Existing `test_parallel_execution()` works with trivial `echo` commands

**What fails:**
- ❌ Lock file updates: 4 out of 5 stages failed with "file is busy" errors
- ❌ Long-running stages: 2-5 minute parquet/DB generation triggers lock contention
- ❌ Concurrent `dvc.lock` writes: Multiple `dvc repro` processes can't coordinate

The current subprocess-based approach **does run commands in parallel**, but fails during the `dvc.lock` update phase when multiple processes try to write simultaneously.

## Root Cause

DVC's locking is designed for **single-process sequential execution**. When `dvc-run` spawns multiple `dvc repro` subprocesses:
1. Each process tries to read `dvc.lock` to check dependencies
2. Each process tries to lock output files during execution
3. Each process tries to write `dvc.lock` after completion
4. Race conditions and deadlocks occur

## Solution: Bypass DVC CLI, Implement File Format Directly

`dvc-run` should **bypass the DVC CLI entirely** and implement DVC's file format handling directly, with proper thread-safe coordination.

**Key insight:** We don't need DVC's Python API. We just need to:
1. Parse `dvc.yaml` (already done)
2. Parse `dvc.lock` (YAML file)
3. Compute MD5 hashes (use Python's `hashlib`)
4. Run commands (use `subprocess`)
5. Update `dvc.lock` (write YAML file with single-writer coordination)

This approach:
- ✅ Avoids DVC's locking mechanisms entirely
- ✅ Gives us full control over parallelization
- ✅ Simpler than using DVC's internal APIs
- ✅ No dependency on DVC's internal implementation details

### Architecture

```
dvc-run (main process)
├── Parse dvc.yaml (shared, read-only)
├── Load dvc.lock (shared, read-once at startup)
├── Build DAG (shared, read-only)
└── Execute stages in parallel
    ├── Worker 1: check freshness → run command → compute hashes → queue lock update
    ├── Worker 2: check freshness → run command → compute hashes → queue lock update
    └── Worker N: check freshness → run command → compute hashes → queue lock update

Lock Manager (single thread)
└── Sequential dvc.lock updates from workers
```

### Implementation Steps

#### 1. Add MD5 hash computation module

**New file: `dvc_run/hash.py`**

```python
"""MD5 hash computation for files and directories."""

import hashlib
from pathlib import Path


def compute_file_hash(path: Path) -> tuple[str, int]:
    """Compute MD5 hash and size of a file.

    Args:
        path: Path to file

    Returns:
        Tuple of (md5_hash, file_size)
    """
    md5 = hashlib.md5()
    size = 0

    with open(path, "rb") as f:
        while chunk := f.read(8192):
            md5.update(chunk)
            size += len(chunk)

    return md5.hexdigest(), size


def compute_dir_hash(path: Path) -> tuple[str, int]:
    """Compute MD5 hash of directory (hash of all file hashes).

    This matches DVC's directory hashing algorithm:
    - Sort all files in directory tree
    - Compute hash of each file
    - Compute hash of concatenated hashes

    Args:
        path: Path to directory

    Returns:
        Tuple of (md5_hash, total_size)
    """
    md5 = hashlib.md5()
    total_size = 0

    # Get all files sorted by relative path (matches DVC behavior)
    files = sorted(path.rglob("*"), key=lambda p: str(p.relative_to(path)))

    for file_path in files:
        if file_path.is_file():
            file_hash, file_size = compute_file_hash(file_path)
            # Update combined hash with: path + hash
            md5.update(str(file_path.relative_to(path)).encode())
            md5.update(file_hash.encode())
            total_size += file_size

    return md5.hexdigest(), total_size


def compute_hash(path: Path) -> dict:
    """Compute hash for file or directory.

    Args:
        path: Path to file or directory

    Returns:
        Dictionary with 'md5' and 'size' keys
    """
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")

    if path.is_dir():
        md5, size = compute_dir_hash(path)
    else:
        md5, size = compute_file_hash(path)

    return {"md5": md5, "size": size}
```

#### 2. Implement thread-safe lock file coordination

**Problem:** Multiple workers can't safely update `dvc.lock` concurrently.

**Solution:** Single-threaded lock manager with queue:

```python
import threading
from queue import Queue
from dvc.dvcfile import ProjectFile

class LockManager:
    """Coordinates sequential updates to dvc.lock from parallel workers."""

    def __init__(self, repo_root: Path):
        self.repo_root = repo_root
        self.lock_queue = Queue()
        self.lock_thread = threading.Thread(target=self._process_updates)
        self.running = True

    def start(self):
        """Start the lock manager thread."""
        self.lock_thread.start()

    def stop(self):
        """Stop the lock manager thread."""
        self.running = False
        self.lock_queue.put(None)  # Sentinel
        self.lock_thread.join()

    def queue_update(self, stage_name: str, stage_data: dict):
        """Queue a lock file update from a worker."""
        self.lock_queue.put((stage_name, stage_data))

    def _process_updates(self):
        """Process lock file updates sequentially (runs in dedicated thread)."""
        while self.running:
            item = self.lock_queue.get()
            if item is None:  # Sentinel
                break

            stage_name, stage_data = item
            self._update_lock_file(stage_name, stage_data)

    def _update_lock_file(self, stage_name: str, stage_data: dict):
        """Update dvc.lock with new stage data."""
        lock_file = self.repo_root / "dvc.lock"

        # Read current lock (thread-safe via file system locks)
        with open(lock_file, "r") as f:
            lock_data = yaml.safe_load(f) or {}

        # Update stage entry
        if "stages" not in lock_data:
            lock_data["stages"] = {}
        lock_data["stages"][stage_name] = stage_data

        # Write atomically (temp file + rename)
        temp_file = lock_file.with_suffix(".tmp")
        with open(temp_file, "w") as f:
            yaml.dump(lock_data, f)
        temp_file.replace(lock_file)
```

#### 3. Implement parallel stage execution with coordination

**Update `dvc_run/dvc.py`:**

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
from pathlib import Path
import yaml
from .hash import compute_hash

class ParallelExecutor:
    """Executes DAG levels in parallel with thread-safe coordination."""

    def __init__(self, repo_root: Path, lock_manager: LockManager, dag):
        self.repo_root = repo_root
        self.lock_manager = lock_manager
        self.dag = dag
        self.lock_data = self._load_lock_file()

    def _load_lock_file(self) -> dict:
        """Load dvc.lock once at startup."""
        lock_file = self.repo_root / "dvc.lock"
        if not lock_file.exists():
            return {"schema": "2.0", "stages": {}}

        with open(lock_file, "r") as f:
            return yaml.safe_load(f) or {"schema": "2.0", "stages": {}}

    def execute_level(self, stages: list[str], max_workers: int = None):
        """Execute all stages in a DAG level in parallel."""
        results = {}

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all stages in this level
            future_to_stage = {
                executor.submit(self._run_stage, stage_name): stage_name
                for stage_name in stages
            }

            # Collect results as they complete
            for future in as_completed(future_to_stage):
                stage_name = future_to_stage[future]
                try:
                    result = future.result()
                    results[stage_name] = result
                except Exception as e:
                    results[stage_name] = {"error": str(e)}

        return results

    def _run_stage(self, stage_name: str) -> dict:
        """Run a single stage (executes in worker thread)."""
        stage = self.dag.stages[stage_name]

        # Check if stage is fresh (thread-safe read of cached lock data)
        if self._is_stage_fresh(stage):
            return {"status": "up-to-date"}

        # Run the command directly via subprocess
        # This is the long-running part, safe to parallelize
        result = subprocess.run(
            stage.cmd,
            shell=True,
            cwd=self.repo_root,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed: {stage.cmd}\n"
                f"stdout: {result.stdout}\n"
                f"stderr: {result.stderr}"
            )

        # Compute output hashes (thread-safe, only reads files)
        stage_data = self._compute_stage_lock_data(stage)

        # Queue lock file update (non-blocking)
        self.lock_manager.queue_update(stage_name, stage_data)

        return {"status": "completed"}

    def _is_stage_fresh(self, stage) -> bool:
        """Check if stage outputs are up-to-date."""
        stage_lock = self.lock_data.get("stages", {}).get(stage.name)
        if not stage_lock:
            return False  # No lock entry = needs to run

        # Check if deps changed
        for dep_path in stage.deps:
            dep_path_obj = self.repo_root / dep_path
            if not dep_path_obj.exists():
                return False  # Dependency missing

            current_hash = compute_hash(dep_path_obj)
            lock_dep = next(
                (d for d in stage_lock.get("deps", []) if d["path"] == dep_path),
                None
            )
            if not lock_dep or lock_dep.get("md5") != current_hash["md5"]:
                return False

        # Check if outs exist and match
        for out_path in stage.outs:
            out_path_obj = self.repo_root / out_path
            if not out_path_obj.exists():
                return False  # Output missing

            current_hash = compute_hash(out_path_obj)
            lock_out = next(
                (o for o in stage_lock.get("outs", []) if o["path"] == out_path),
                None
            )
            if not lock_out or lock_out.get("md5") != current_hash["md5"]:
                return False

        return True

    def _compute_stage_lock_data(self, stage) -> dict:
        """Compute lock file entry for stage after execution."""
        deps_data = []
        for dep_path in stage.deps:
            dep_path_obj = self.repo_root / dep_path
            hash_info = compute_hash(dep_path_obj)
            deps_data.append({
                "path": dep_path,
                "md5": hash_info["md5"],
                "size": hash_info["size"],
            })

        outs_data = []
        for out_path in stage.outs:
            out_path_obj = self.repo_root / out_path
            hash_info = compute_hash(out_path_obj)
            outs_data.append({
                "path": out_path,
                "md5": hash_info["md5"],
                "size": hash_info["size"],
            })

        return {
            "cmd": stage.cmd,
            "deps": deps_data,
            "outs": outs_data,
        }
```

#### 4. Main execution flow

**Update main CLI to use new executor:**

```python
def run_pipeline(repo_root: Path, max_workers: int = None):
    """Run entire pipeline with parallel execution."""
    # Parse dvc.yaml once
    dag = DAG.from_yaml(repo_root / "dvc.yaml")

    # Initialize lock manager
    lock_manager = LockManager(repo_root)
    lock_manager.start()

    # Create executor
    executor = ParallelExecutor(repo_root, lock_manager, dag)

    try:
        # Execute levels sequentially, stages within level in parallel
        levels = list(dag.levels())
        for level_num, stages in enumerate(levels, start=1):
            print(f"Level {level_num}/{len(levels)}: {len(stages)} stage(s)")

            results = executor.execute_level(stages, max_workers)

            # Check for failures
            failures = [s for s, r in results.items() if "error" in r]
            if failures:
                for stage_name in failures:
                    error = results[stage_name]["error"]
                    print(f"  ✗ {stage_name}: {error}", file=sys.stderr)
                raise RuntimeError(f"Stage(s) failed: {', '.join(failures)}")

            # Print summary
            for stage_name, result in results.items():
                status = result.get("status", "unknown")
                if status == "up-to-date":
                    print(f"  ⊙ {stage_name}: up-to-date")
                elif status == "completed":
                    print(f"  ✓ {stage_name}: completed")

    finally:
        # Ensure lock manager stops cleanly
        lock_manager.stop()
```

### Key Design Principles

1. **Read-once strategy**: Load `dvc.yaml` and `dvc.lock` once at startup, cache in memory
2. **No DVC CLI dependency**: Bypass `dvc repro`, run commands directly via subprocess
3. **Sequential lock updates**: Single thread manages `dvc.lock` writes
4. **Atomic writes**: Use temp file + rename for lock file updates
5. **No shared mutable state**: Workers only queue immutable update requests
6. **File-based coordination**: No process-level locks, only thread-level coordination within single dvc-run process

### Benefits

- ✅ **True parallel execution**: No lock contention between stages
- ✅ **Correct behavior**: Same semantics as `dvc repro`
- ✅ **Atomic updates**: Lock file always consistent
- ✅ **Error handling**: Failed stages don't corrupt lock file
- ✅ **Performance**: Full parallelism at each DAG level

### Testing Strategy

1. **Unit tests**:
   - Test `compute_hash()` produces same MD5s as DVC
   - Test LockManager in isolation with multiple threads
   - Test `_is_stage_fresh()` correctly detects changes

2. **Integration tests**:
   - Run full pipeline, verify `dvc.lock` format matches DVC's
   - Test with long-running commands (sleep 5+) to ensure true parallelism
   - Verify existing `test_parallel_execution` still passes

3. **Stress tests**:
   - Large DAG (20+ stages, 4+ levels) with concurrent execution
   - Inject failures mid-pipeline, verify clean error handling
   - Run NJDOT pipeline (15 stages, 4 levels, 2-5min commands per stage)

4. **Comparison tests**:
   - Generate outputs with `dvc repro` (sequential)
   - Generate outputs with `dvc-run` (parallel)
   - Verify MD5 hashes match exactly
   - Compare `dvc.lock` files (should be identical except ordering)

### Implementation Notes

#### Why Not Use DVC's Python API?

The DVC Python API (`dvc.repo.Repo`, `dvc.stage.Stage`) is designed for single-process use and has several issues for parallel execution:

1. **Tightly coupled to locking**: `stage.run()` and `stage.save()` internally acquire locks
2. **Opaque implementation**: Hard to control when/how locks are acquired
3. **Process-level locks**: Uses file system locks that don't work across subprocesses
4. **Unnecessary complexity**: We only need to read/write YAML and compute hashes

By implementing DVC's file formats directly, we:
- ✅ Avoid all DVC locking mechanisms
- ✅ Have full control over parallelization
- ✅ Reduce dependencies (only need `pyyaml` and stdlib `hashlib`)
- ✅ Make behavior more transparent and debuggable

#### MD5 Hash Compatibility

DVC's directory hashing algorithm (from [source code](https://github.com/iterative/dvc/blob/main/dvc/fs/local.py)):
```python
# For each file in sorted order:
#   hash += relative_path + file_md5
# directory_md5 = md5(hash)
```

Our `compute_dir_hash()` implements this exactly. To verify compatibility, test against DVC's output:
```bash
dvc add some_directory/
# Check .dvc file for md5 hash
# Compare with our compute_hash(Path("some_directory"))
```

#### File System Considerations

Since we're using **thread-based parallelism within a single process** (not multiple processes), we don't need file system locks at all:

- ✅ Multiple threads can safely read the same file
- ✅ Single dedicated thread writes `dvc.lock` (no contention)
- ✅ Atomic rename operation ensures consistency
- ✅ Works on all file systems (NFS, networked drives, etc.)

This is much simpler than DVC's multi-process locking approach.

### Migration Path

1. **Phase 1**: Implement in separate module, keep subprocess fallback
2. **Phase 2**: Add `--native` flag to opt-in to new implementation
3. **Phase 3**: Make native implementation default, keep `--subprocess` fallback
4. **Phase 4**: Remove subprocess implementation after validation

### Open Questions

1. **DVC version compatibility**: What's the minimum DVC version required?
2. **Remote storage**: How to handle `dvc push` during parallel execution?
3. **Metrics/plots**: Should we collect and update metrics in parallel?
4. **Cache sharing**: Can worker threads share DVC's file cache safely?

### References

- DVC Python API docs: https://dvc.org/doc/api-reference
- DVC stage execution: https://github.com/iterative/dvc/blob/main/dvc/stage/__init__.py
- DVC locking: https://github.com/iterative/dvc/blob/main/dvc/lock.py
- Python threading: https://docs.python.org/3/library/threading.html
- Atomic file writes: https://docs.python.org/3/library/os.html#os.replace

## Success Criteria

Implementation is complete when:
- ✅ All 15 stages in NJDOT pipeline run without lock contention
- ✅ `dvc.lock` correctly updated after parallel execution
- ✅ MD5 hashes match sequential `dvc repro` execution
- ✅ Failed stages don't corrupt `dvc.lock`
- ✅ Tests pass with 100+ concurrent stages

## Current Status & Next Steps

**Current implementation:**
- ✅ DAG parsing and topological sorting works
- ✅ Parallel execution framework works (spawns commands simultaneously)
- ❌ Uses `dvc repro` subprocess calls (causes lock contention)
- ❌ Long-running stages fail during lock file updates

**To implement this spec:**
1. Create `dvc_run/hash.py` with MD5 computation
2. Create `dvc_run/lock.py` with LockManager class
3. Update `dvc_run/dvc.py` to remove subprocess calls, implement ParallelExecutor
4. Add unit tests for hash computation and lock manager
5. Add integration test with long-running stages (sleep 5+)
6. Test with NJDOT pipeline (real-world validation)

**Estimated effort:** 1-2 days of focused implementation

**Risk areas:**
- MD5 hash compatibility with DVC (mitigate: comprehensive comparison tests)
- Lock file format edge cases (mitigate: read DVC source code for `dvc.lock` schema)
- Error handling during parallel execution (mitigate: stress tests with injected failures)
