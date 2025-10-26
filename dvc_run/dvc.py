"""Integration with DVC CLI commands."""

import subprocess
from dataclasses import dataclass


@dataclass
class StageStatus:
    """Status of a DVC stage."""

    name: str
    is_fresh: bool
    message: str = ""


class DVCClient:
    """Client for interacting with DVC CLI."""

    def check_stage_status(self, stage_name: str) -> StageStatus:
        """Check if a stage is up-to-date using dvc status.

        Args:
            stage_name: Name of the stage to check

        Returns:
            StageStatus indicating if stage needs to be run

        Note:
            A stage is considered fresh (up-to-date) if:
            - dvc status returns 0
            - AND output says "up to date" or doesn't list changes
        """
        try:
            result = subprocess.run(
                ["dvc", "status", stage_name],
                capture_output=True,
                text=True,
                check=False,
            )

            # Stage is fresh if status returns 0 and either:
            # - Output is empty, OR
            # - Output says "up to date"
            stdout = result.stdout.strip()
            is_fresh = (
                result.returncode == 0 and
                (not stdout or "up to date" in stdout.lower())
            )

            return StageStatus(
                name=stage_name,
                is_fresh=is_fresh,
                message=result.stdout.strip() if result.stdout.strip() else "up to date",
            )
        except FileNotFoundError:
            raise RuntimeError(
                "dvc command not found - is DVC installed? "
                "Install with: pip install dvc"
            )

    def run_stage(self, stage_name: str) -> subprocess.CompletedProcess:
        """Run a stage using dvc repro.

        Args:
            stage_name: Name of the stage to run

        Returns:
            CompletedProcess with results

        Raises:
            subprocess.CalledProcessError: If dvc repro fails
        """
        try:
            result = subprocess.run(
                ["dvc", "repro", stage_name],
                capture_output=True,
                text=True,
                check=True,
            )
            return result
        except subprocess.CalledProcessError as e:
            # Re-raise with better error message
            raise RuntimeError(
                f"Stage '{stage_name}' failed:\n"
                f"stdout: {e.stdout}\n"
                f"stderr: {e.stderr}"
            ) from e
        except FileNotFoundError:
            raise RuntimeError(
                "dvc command not found - is DVC installed? "
                "Install with: pip install dvc"
            )
