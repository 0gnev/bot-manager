from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_taskfile_exposes_rollout_tasks() -> None:
    taskfile = (ROOT / "Taskfile.yml").read_text(encoding="utf-8")
    assert "pg-first-rollout:" in taskfile
    assert "pg-rollback:" in taskfile


def test_first_rollout_script_contains_backup_import_and_manifest_flow() -> None:
    script = (ROOT / "scripts/postgres-first-rollout.sh").read_text(encoding="utf-8")
    assert "pg_dump -U bridge -d bridge -Fc" in script
    assert "python -m scripts.import_json_state" in script
    assert "manifest.env" in script
    assert "IMPORT_LEGACY_STATE" in script


def test_rollback_script_restores_database_dump_and_state_archive() -> None:
    script = (ROOT / "scripts/postgres-rollback.sh").read_text(encoding="utf-8")
    assert "pg_restore --clean --if-exists" in script
    assert "STATE_ARCHIVE_PATH" in script
    assert "find \"${STATE_PATH}\" -mindepth 1 ! -name '.gitkeep' -exec rm -rf {} +" in script
