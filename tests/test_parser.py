"""Tests for dvc.yaml parser."""

import tempfile
from pathlib import Path

import pytest

from dvc_run.parser import DVCYamlParser


def test_parse_simple_stages():
    """Test parsing simple stage definitions."""
    yaml_content = """
stages:
  stage_a:
    cmd: echo "test" > output.txt
    deps:
      - input.txt
    outs:
      - output.txt

  stage_b:
    cmd: cat input.txt
    deps:
      - input.txt
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = Path(f.name)

    try:
        parser = DVCYamlParser(yaml_path)
        stages = parser.parse()

        assert len(stages) == 2

        stage_a = next(s for s in stages if s.name == "stage_a")
        assert stage_a.cmd == 'echo "test" > output.txt'
        assert stage_a.deps == ["input.txt"]
        assert stage_a.outs == ["output.txt"]

        stage_b = next(s for s in stages if s.name == "stage_b")
        assert stage_b.cmd == "cat input.txt"
        assert stage_b.deps == ["input.txt"]
        assert stage_b.outs == []
    finally:
        yaml_path.unlink()


def test_parse_missing_file():
    """Test error handling for missing dvc.yaml."""
    parser = DVCYamlParser(Path("/nonexistent/dvc.yaml"))

    with pytest.raises(FileNotFoundError):
        parser.parse()


def test_parse_missing_cmd():
    """Test error handling for stage without cmd."""
    yaml_content = """
stages:
  bad_stage:
    deps:
      - input.txt
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = Path(f.name)

    try:
        parser = DVCYamlParser(yaml_path)
        with pytest.raises(ValueError, match="missing required 'cmd' field"):
            parser.parse()
    finally:
        yaml_path.unlink()


def test_parse_list_cmd():
    """Test parsing stages with list-style commands."""
    yaml_content = """
stages:
  multi_cmd:
    cmd:
      - echo "step 1"
      - echo "step 2"
      - echo "step 3"
    outs:
      - output.txt
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = Path(f.name)

    try:
        parser = DVCYamlParser(yaml_path)
        stages = parser.parse()

        assert len(stages) == 1
        assert stages[0].cmd == 'echo "step 1" && echo "step 2" && echo "step 3"'
    finally:
        yaml_path.unlink()


def test_parse_no_stages():
    """Test error handling for yaml without stages section."""
    yaml_content = """
vars:
  - data_dir: /data
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        f.write(yaml_content)
        f.flush()
        yaml_path = Path(f.name)

    try:
        parser = DVCYamlParser(yaml_path)
        with pytest.raises(ValueError, match="must contain 'stages' section"):
            parser.parse()
    finally:
        yaml_path.unlink()
