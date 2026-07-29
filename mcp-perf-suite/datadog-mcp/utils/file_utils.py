# datadog-mcp/utils/file_utils.py

import os
import re
import shutil
from pathlib import Path
from typing import List


# -----------------------------------------------
# Backup utilities
# -----------------------------------------------

def backup_file(file_path: str, backups_dir: str = None) -> str:
    """
    Move an existing file into a backups subfolder with an incrementing suffix.

    If the file does not exist, returns an empty string (no-op).
    The backup directory defaults to a ``backups/`` subfolder alongside
    the original file.

    Naming pattern:
        {original_stem}_{NNNNNN}{original_suffix}
        e.g.  kpi_metrics_[my-svc]_000001.csv

    Args:
        file_path: Absolute path to the file that should be backed up.
        backups_dir: Optional override for the backup directory.
                     Defaults to ``<parent_of_file>/backups/``.

    Returns:
        str: The path the file was moved to, or empty string if
             the source file did not exist.
    """
    src = Path(file_path)
    if not src.exists():
        return ""

    if backups_dir:
        dest_dir = Path(backups_dir)
    else:
        dest_dir = src.parent / "backups"

    dest_dir.mkdir(parents=True, exist_ok=True)

    next_num = _next_backup_number(dest_dir, src.stem, src.suffix)
    backup_name = f"{src.stem}_{next_num:06d}{src.suffix}"
    dest = dest_dir / backup_name

    shutil.move(str(src), str(dest))
    return str(dest)


def backup_matching_files(directory: str, pattern: str, backups_dir: str = None) -> List[str]:
    """
    Back up all files in *directory* whose names match *pattern*.

    Useful for backing up all ``kpi_metrics_*.csv`` files before a re-run.

    Args:
        directory: Directory to scan for matching files.
        pattern: Glob-style pattern (e.g. ``kpi_metrics_*.csv``).
        backups_dir: Optional override for backup destination.

    Returns:
        list[str]: Paths of the created backup files.
    """
    src_dir = Path(directory)
    if not src_dir.is_dir():
        return []

    backed_up: List[str] = []
    for match in sorted(src_dir.glob(pattern)):
        if match.is_file():
            result = backup_file(str(match), backups_dir)
            if result:
                backed_up.append(result)

    return backed_up


# -----------------------------------------------
# Internal helpers
# -----------------------------------------------

def _next_backup_number(backups_dir: Path, stem: str, suffix: str) -> int:
    """
    Scan *backups_dir* for existing backups of a file and return the next
    available incrementing number.

    Looks for files matching ``{stem}_{NNNNNN}{suffix}`` and returns
    max(existing) + 1, or 1 if no backups exist yet.
    """
    # Escape the stem so square-bracket filenames don't break the regex
    escaped_stem = re.escape(stem)
    pattern = re.compile(rf"^{escaped_stem}_(\d{{6}}){re.escape(suffix)}$")

    max_num = 0
    for entry in backups_dir.iterdir():
        m = pattern.match(entry.name)
        if m:
            max_num = max(max_num, int(m.group(1)))

    return max_num + 1
