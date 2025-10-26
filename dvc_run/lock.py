"""Parser and manager for dvc.lock files."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from filelock import FileLock

from dvc_run.hash import compute_file_size
from dvc_run.stage import Stage


@dataclass
class FileInfo:
    """Information about a file tracked in dvc.lock."""

    path: str
    md5: str
    size: int


@dataclass
class StageState:
    """Recorded state of a stage from dvc.lock."""

    cmd: str
    deps: dict[str, FileInfo] = field(default_factory=dict)
    outs: dict[str, FileInfo] = field(default_factory=dict)


class DVCLockParser:
    """Parser for dvc.lock files."""

    def __init__(self, lock_path: Path = Path("dvc.lock")):
        self.lock_path = lock_path

    def parse(self) -> dict[str, StageState]:
        """Parse dvc.lock and return recorded state per stage.

        Returns:
            Dict mapping stage_name → StageState with recorded hashes.
            Returns empty dict if lock file doesn't exist.
        """
        if not self.lock_path.exists():
            return {}

        with open(self.lock_path) as f:
            lock = yaml.safe_load(f)

        if not lock or 'stages' not in lock:
            return {}

        stages = {}
        for stage_name, stage_data in lock['stages'].items():
            stages[stage_name] = self._parse_stage(stage_data)

        return stages

    def _parse_stage(self, stage_data: dict) -> StageState:
        """Parse a single stage entry from dvc.lock.

        Args:
            stage_data: Stage data from lock file

        Returns:
            StageState with command and file information
        """
        cmd = stage_data.get('cmd', '')

        # Parse dependencies
        deps = {}
        for dep in stage_data.get('deps', []):
            if isinstance(dep, dict) and 'path' in dep:
                deps[dep['path']] = FileInfo(
                    path=dep['path'],
                    md5=dep.get('md5', ''),
                    size=dep.get('size', 0),
                )

        # Parse outputs
        outs = {}
        for out in stage_data.get('outs', []):
            if isinstance(out, dict) and 'path' in out:
                outs[out['path']] = FileInfo(
                    path=out['path'],
                    md5=out.get('md5', ''),
                    size=out.get('size', 0),
                )

        return StageState(cmd=cmd, deps=deps, outs=outs)


class DVCLockWriter:
    """Thread-safe writer for dvc.lock files."""

    def __init__(self, lock_path: Path = Path("dvc.lock")):
        """Initialize lock writer.

        Args:
            lock_path: Path to dvc.lock file
        """
        self.lock_path = lock_path
        # Use .lock suffix for file lock (will be gitignored)
        self.file_lock = FileLock(str(lock_path) + ".lock")

    def update_stage(
        self,
        stage: Stage,
        deps_hashes: dict[str, str],
        outs_hashes: dict[str, str],
    ):
        """Update a single stage in dvc.lock (thread-safe).

        Args:
            stage: Stage that just completed
            deps_hashes: {path: md5} for all dependencies
            outs_hashes: {path: md5} for all outputs
        """
        with self.file_lock:
            # Read current lock state
            if self.lock_path.exists():
                with open(self.lock_path) as f:
                    lock = yaml.safe_load(f) or {}
            else:
                lock = {}

            # Ensure structure exists
            if 'schema' not in lock:
                lock['schema'] = '2.0'
            if 'stages' not in lock:
                lock['stages'] = {}

            # Build stage entry
            stage_entry = {'cmd': stage.cmd}

            # Add dependencies
            if deps_hashes:
                stage_entry['deps'] = [
                    {
                        'path': path,
                        'md5': md5,
                        'size': compute_file_size(Path(path)),
                    }
                    for path, md5 in sorted(deps_hashes.items())
                ]

            # Add outputs
            if outs_hashes:
                stage_entry['outs'] = [
                    {
                        'path': path,
                        'md5': md5,
                        'size': compute_file_size(Path(path)),
                    }
                    for path, md5 in sorted(outs_hashes.items())
                ]

            # Update stage in lock
            lock['stages'][stage.name] = stage_entry

            # Atomic write (write to temp, then rename)
            temp_path = self.lock_path.with_suffix('.tmp')
            with open(temp_path, 'w') as f:
                yaml.dump(lock, f, sort_keys=False, default_flow_style=False)
            temp_path.rename(self.lock_path)
