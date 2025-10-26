"""Stage freshness checking based on hash comparison."""

from pathlib import Path

from dvc_run.hash import compute_md5
from dvc_run.lock import StageState
from dvc_run.stage import Stage


def is_stage_fresh(stage: Stage, lock_state: StageState | None) -> bool:
    """Check if a stage is up-to-date and can be skipped.

    A stage is fresh (up-to-date) if:
    1. It exists in dvc.lock
    2. Its command hasn't changed
    3. All dependency hashes match current file hashes
    4. All output hashes match current file hashes

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

    # Check all dependencies exist and match recorded hashes
    for dep_path in stage.deps:
        if not _check_file_hash(dep_path, lock_state.deps.get(dep_path)):
            return False

    # Check all outputs exist and match recorded hashes
    for out_path in stage.outs:
        if not _check_file_hash(out_path, lock_state.outs.get(out_path)):
            return False

    # All checks passed!
    return True


def _check_file_hash(file_path: str, recorded_info) -> bool:
    """Check if a file's current hash matches the recorded hash.

    Args:
        file_path: Path to file to check
        recorded_info: FileInfo from lock file (None if not recorded)

    Returns:
        True if file exists and hash matches recorded hash
    """
    path = Path(file_path)

    # File not recorded in lock = not fresh
    if recorded_info is None:
        return False

    # File missing = not fresh
    if not path.exists():
        return False

    # Compute current hash and compare
    try:
        current_md5 = compute_md5(path)
        return current_md5 == recorded_info.md5
    except (FileNotFoundError, ValueError):
        # Error computing hash = not fresh
        return False


def get_freshness_reason(stage: Stage, lock_state: StageState | None) -> str:
    """Get human-readable reason why a stage is not fresh.

    Args:
        stage: Stage definition from dvc.yaml
        lock_state: Recorded state from dvc.lock

    Returns:
        String describing why stage needs to run, or "up-to-date" if fresh
    """
    if lock_state is None:
        return "never run before"

    if stage.cmd != lock_state.cmd:
        return "command changed"

    # Check dependencies
    for dep_path in stage.deps:
        path = Path(dep_path)
        recorded_info = lock_state.deps.get(dep_path)

        if recorded_info is None:
            return f"new dependency: {dep_path}"

        if not path.exists():
            return f"missing dependency: {dep_path}"

        try:
            current_md5 = compute_md5(path)
            if current_md5 != recorded_info.md5:
                return f"dependency changed: {dep_path}"
        except (FileNotFoundError, ValueError):
            return f"error reading dependency: {dep_path}"

    # Check outputs
    for out_path in stage.outs:
        path = Path(out_path)
        recorded_info = lock_state.outs.get(out_path)

        if recorded_info is None:
            return f"new output: {out_path}"

        if not path.exists():
            return f"missing output: {out_path}"

        try:
            current_md5 = compute_md5(path)
            if current_md5 != recorded_info.md5:
                return f"output changed: {out_path}"
        except (FileNotFoundError, ValueError):
            return f"error reading output: {out_path}"

    return "up-to-date"
