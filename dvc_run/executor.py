"""Parallel executor for DVC pipeline stages."""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from dvc_run.dag import DAG
from dvc_run.dvc import DVCClient
from dvc_run.freshness import get_freshness_reason, is_stage_fresh
from dvc_run.hash import compute_md5
from dvc_run.lock import DVCLockParser, DVCLockWriter


@dataclass
class ExecutionResult:
    """Result of executing a stage."""

    stage_name: str
    success: bool
    skipped: bool = False
    message: str = ""


class ParallelExecutor:
    """Execute DVC pipeline stages in parallel."""

    def __init__(
        self,
        dag: DAG,
        max_workers: int | None = None,
        dry_run: bool = False,
        output: TextIO = sys.stderr,
        lock_path: Path = Path("dvc.lock"),
        use_lock: bool = True,
        update_lock: bool = True,
    ):
        """Initialize parallel executor.

        Args:
            dag: Dependency graph of stages
            max_workers: Maximum number of parallel workers (default: CPU count)
            dry_run: If True, don't actually run stages
            output: Stream for logging output (default: stderr)
            lock_path: Path to dvc.lock file
            use_lock: If True, use dvc.lock for freshness checking (default: True)
            update_lock: If True, update dvc.lock after each stage (default: True)
        """
        self.dag = dag
        self.max_workers = max_workers
        self.dry_run = dry_run
        self.output = output
        self.lock_path = lock_path
        self.use_lock = use_lock
        self.update_lock = update_lock
        self.dvc = DVCClient()

        # Parse dvc.lock if using lock-based freshness
        self.lock_states = {}
        if self.use_lock:
            lock_parser = DVCLockParser(lock_path)
            self.lock_states = lock_parser.parse()

        # Initialize lock writer if we'll be updating
        self.lock_writer = None
        if self.update_lock and not self.dry_run:
            self.lock_writer = DVCLockWriter(lock_path)

    def execute(self) -> list[ExecutionResult]:
        """Execute all stages in the DAG, respecting dependencies.

        Returns:
            List of ExecutionResult for each stage

        Raises:
            RuntimeError: If any stage fails
        """
        levels = self.dag.topological_sort()

        self._log(f"Execution plan ({len(levels)} levels, {len(self.dag.stages)} stages):")
        for i, level in enumerate(levels, 1):
            self._log(f"  Level {i}: {', '.join(level)}")

        if self.dry_run:
            self._log("\nDry run - no stages will be executed")
            return []

        self._log("")  # blank line before execution

        results = []
        for level_num, level in enumerate(levels, 1):
            self._log(f"Level {level_num}/{len(levels)}: {len(level)} stage(s)")
            level_results = self._execute_level(level)
            results.extend(level_results)

            # Check for failures
            failures = [r for r in level_results if not r.success and not r.skipped]
            if failures:
                failed_stages = ", ".join(r.stage_name for r in failures)
                raise RuntimeError(f"Stage(s) failed: {failed_stages}")

        return results

    def _execute_level(self, stage_names: list[str]) -> list[ExecutionResult]:
        """Execute all stages in a level in parallel.

        Args:
            stage_names: List of stage names to execute

        Returns:
            List of ExecutionResult, one per stage
        """
        if len(stage_names) == 1:
            # Single stage - run directly without thread pool overhead
            return [self._execute_stage(stage_names[0])]

        # Multiple stages - run in parallel
        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._execute_stage, stage_name): stage_name
                for stage_name in stage_names
            }

            for future in as_completed(futures):
                stage_name = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    self._log(f"  ✗ {stage_name}: {e}")
                    results.append(
                        ExecutionResult(
                            stage_name=stage_name,
                            success=False,
                            message=str(e),
                        )
                    )

        return results

    def _execute_stage(self, stage_name: str) -> ExecutionResult:
        """Execute a single stage.

        Args:
            stage_name: Name of stage to execute

        Returns:
            ExecutionResult for this stage
        """
        stage = self.dag.stages[stage_name]

        # Check if stage is already up-to-date
        if self.use_lock:
            # Use lock-based freshness checking
            lock_state = self.lock_states.get(stage_name)
            if is_stage_fresh(stage, lock_state):
                reason = get_freshness_reason(stage, lock_state)
                self._log(f"  ⊙ {stage_name}: {reason}")
                return ExecutionResult(
                    stage_name=stage_name,
                    success=True,
                    skipped=True,
                    message=reason,
                )
        else:
            # Fall back to dvc status (legacy behavior)
            status = self.dvc.check_stage_status(stage_name)
            if status.is_fresh:
                self._log(f"  ⊙ {stage_name}: {status.message}")
                return ExecutionResult(
                    stage_name=stage_name,
                    success=True,
                    skipped=True,
                    message=status.message,
                )

        # Run the stage
        self._log(f"  ⟳ {stage_name}: running...")
        try:
            self.dvc.run_stage(stage_name)

            # If updating lock, compute hashes and update
            if self.lock_writer:
                deps_hashes = {}
                for dep_path in stage.deps:
                    try:
                        deps_hashes[dep_path] = compute_md5(Path(dep_path))
                    except (FileNotFoundError, ValueError) as e:
                        self._log(f"  ⚠ {stage_name}: warning - couldn't hash dep {dep_path}: {e}")

                outs_hashes = {}
                for out_path in stage.outs:
                    try:
                        outs_hashes[out_path] = compute_md5(Path(out_path))
                    except (FileNotFoundError, ValueError) as e:
                        self._log(f"  ⚠ {stage_name}: warning - couldn't hash out {out_path}: {e}")

                # Update lock file (thread-safe)
                self.lock_writer.update_stage(stage, deps_hashes, outs_hashes)

            self._log(f"  ✓ {stage_name}: completed")
            return ExecutionResult(
                stage_name=stage_name,
                success=True,
                message="completed",
            )
        except RuntimeError as e:
            self._log(f"  ✗ {stage_name}: failed")
            return ExecutionResult(
                stage_name=stage_name,
                success=False,
                message=str(e),
            )

    def _log(self, message: str):
        """Write log message to output stream."""
        print(message, file=self.output)
