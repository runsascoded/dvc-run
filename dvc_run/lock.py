"""Parser and manager for dvc.lock files."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


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
