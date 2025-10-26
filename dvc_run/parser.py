"""Parser for dvc.yaml pipeline files."""

from pathlib import Path
from typing import Any

import yaml

from dvc_run.stage import Stage


class DVCYamlParser:
    """Parse dvc.yaml files and extract stage definitions."""

    def __init__(self, dvc_yaml_path: Path = Path("dvc.yaml")):
        self.dvc_yaml_path = dvc_yaml_path

    def parse(self) -> list[Stage]:
        """Parse dvc.yaml and return list of Stage objects.

        Returns:
            List of Stage objects, one per stage in dvc.yaml

        Raises:
            FileNotFoundError: If dvc.yaml doesn't exist
            ValueError: If dvc.yaml is malformed
        """
        if not self.dvc_yaml_path.exists():
            raise FileNotFoundError(f"dvc.yaml not found at {self.dvc_yaml_path}")

        with open(self.dvc_yaml_path) as f:
            data = yaml.safe_load(f)

        if not data or "stages" not in data:
            raise ValueError("dvc.yaml must contain 'stages' section")

        stages = []
        for stage_name, stage_config in data["stages"].items():
            stages.append(self._parse_stage(stage_name, stage_config))

        return stages

    def _parse_stage(self, name: str, config: dict[str, Any]) -> Stage:
        """Parse a single stage configuration.

        Args:
            name: Stage name
            config: Stage configuration dict

        Returns:
            Stage object

        Raises:
            ValueError: If stage config is invalid
        """
        if "cmd" not in config:
            raise ValueError(f"Stage '{name}' missing required 'cmd' field")

        # Extract command (can be string or list)
        cmd = config["cmd"]
        if isinstance(cmd, list):
            cmd = " && ".join(cmd)

        # Extract dependencies
        deps = config.get("deps", [])
        if isinstance(deps, dict):
            # Handle params files and other dependency types
            deps = list(deps.values())
        elif not isinstance(deps, list):
            deps = [deps]

        # Extract outputs
        outs = config.get("outs", [])
        if isinstance(outs, dict):
            outs = list(outs.values())
        elif not isinstance(outs, list):
            outs = [outs]

        # Extract description
        desc = config.get("desc")

        return Stage(
            name=name,
            cmd=cmd,
            deps=deps,
            outs=outs,
            desc=desc,
        )
