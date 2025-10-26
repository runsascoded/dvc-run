"""Tests for dvc.lock parsing and freshness checking."""

import tempfile
from pathlib import Path


from dvc_run.freshness import get_freshness_reason, is_stage_fresh
from dvc_run.hash import compute_md5
from dvc_run.lock import DVCLockParser, FileInfo, StageState
from dvc_run.stage import Stage


def test_parse_lock_file():
    """Test parsing a basic dvc.lock file."""
    lock_content = """
schema: '2.0'
stages:
  stage_a:
    cmd: echo "test" > output.txt
    deps:
      - path: input.txt
        md5: abc123
        size: 1024
    outs:
      - path: output.txt
        md5: def456
        size: 2048
"""

    with tempfile.NamedTemporaryFile(mode='w', suffix='.lock', delete=False) as f:
        f.write(lock_content)
        f.flush()
        lock_path = Path(f.name)

    try:
        parser = DVCLockParser(lock_path)
        stages = parser.parse()

        assert len(stages) == 1
        assert 'stage_a' in stages

        stage = stages['stage_a']
        assert stage.cmd == 'echo "test" > output.txt'
        assert 'input.txt' in stage.deps
        assert stage.deps['input.txt'].md5 == 'abc123'
        assert stage.deps['input.txt'].size == 1024
        assert 'output.txt' in stage.outs
        assert stage.outs['output.txt'].md5 == 'def456'
        assert stage.outs['output.txt'].size == 2048
    finally:
        lock_path.unlink()


def test_parse_missing_lock():
    """Test parsing when lock file doesn't exist."""
    parser = DVCLockParser(Path("/nonexistent/dvc.lock"))
    stages = parser.parse()

    assert stages == {}


def test_compute_file_hash():
    """Test MD5 computation for files."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("test content\n")
        f.flush()
        file_path = Path(f.name)

    try:
        md5 = compute_md5(file_path)
        assert isinstance(md5, str)
        assert len(md5) == 32  # MD5 is 32 hex chars

        # Hash should be deterministic
        md5_2 = compute_md5(file_path)
        assert md5 == md5_2
    finally:
        file_path.unlink()


def test_compute_directory_hash():
    """Test MD5 computation for directories."""
    with tempfile.TemporaryDirectory() as tmpdir:
        dir_path = Path(tmpdir)

        # Create some files
        (dir_path / "file1.txt").write_text("content 1")
        (dir_path / "file2.txt").write_text("content 2")
        (dir_path / "subdir").mkdir()
        (dir_path / "subdir" / "file3.txt").write_text("content 3")

        md5 = compute_md5(dir_path)
        assert isinstance(md5, str)
        assert len(md5) == 32

        # Hash should change if contents change
        (dir_path / "file1.txt").write_text("modified")
        md5_modified = compute_md5(dir_path)
        assert md5 != md5_modified


def test_stage_fresh_no_lock():
    """Test that stage is not fresh when no lock state exists."""
    stage = Stage(
        name="test",
        cmd="echo test",
        deps=[],
        outs=["output.txt"],
    )

    assert not is_stage_fresh(stage, None)
    assert get_freshness_reason(stage, None) == "never run before"


def test_stage_fresh_cmd_changed():
    """Test that stage is not fresh when command changed."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("test")
        f.flush()
        file_path = Path(f.name)

    try:
        md5 = compute_md5(file_path)

        stage = Stage(
            name="test",
            cmd="echo modified",
            deps=[],
            outs=[str(file_path)],
        )

        lock_state = StageState(
            cmd="echo original",
            outs={str(file_path): FileInfo(str(file_path), md5, file_path.stat().st_size)},
        )

        assert not is_stage_fresh(stage, lock_state)
        assert get_freshness_reason(stage, lock_state) == "command changed"
    finally:
        file_path.unlink()


def test_stage_fresh_dep_changed():
    """Test that stage is not fresh when dependency changed."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write("original content")
        f.flush()
        dep_path = Path(f.name)

    try:
        original_md5 = compute_md5(dep_path)

        stage = Stage(
            name="test",
            cmd="cat input.txt",
            deps=[str(dep_path)],
            outs=[],
        )

        lock_state = StageState(
            cmd="cat input.txt",
            deps={str(dep_path): FileInfo(str(dep_path), original_md5, dep_path.stat().st_size)},
        )

        # Initially fresh
        assert is_stage_fresh(stage, lock_state)

        # Modify dependency
        dep_path.write_text("modified content")

        # Now not fresh
        assert not is_stage_fresh(stage, lock_state)
        assert "dependency changed" in get_freshness_reason(stage, lock_state)
    finally:
        dep_path.unlink()


def test_stage_fresh_output_missing():
    """Test that stage is not fresh when output is missing."""
    stage = Stage(
        name="test",
        cmd="echo test",
        deps=[],
        outs=["missing_output.txt"],
    )

    lock_state = StageState(
        cmd="echo test",
        outs={"missing_output.txt": FileInfo("missing_output.txt", "abc123", 1024)},
    )

    assert not is_stage_fresh(stage, lock_state)
    assert "missing output" in get_freshness_reason(stage, lock_state)


def test_stage_fresh_all_match():
    """Test that stage is fresh when everything matches."""
    with tempfile.TemporaryDirectory() as tmpdir:
        dir_path = Path(tmpdir)
        dep_path = dir_path / "input.txt"
        out_path = dir_path / "output.txt"

        dep_path.write_text("input content")
        out_path.write_text("output content")

        dep_md5 = compute_md5(dep_path)
        out_md5 = compute_md5(out_path)

        stage = Stage(
            name="test",
            cmd="cat input.txt > output.txt",
            deps=[str(dep_path)],
            outs=[str(out_path)],
        )

        lock_state = StageState(
            cmd="cat input.txt > output.txt",
            deps={str(dep_path): FileInfo(str(dep_path), dep_md5, dep_path.stat().st_size)},
            outs={str(out_path): FileInfo(str(out_path), out_md5, out_path.stat().st_size)},
        )

        assert is_stage_fresh(stage, lock_state)
        assert get_freshness_reason(stage, lock_state) == "up-to-date"
