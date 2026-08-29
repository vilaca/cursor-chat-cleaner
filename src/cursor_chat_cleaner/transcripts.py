from __future__ import annotations

from pathlib import Path


def transcript_dirs(projects_dir: Path, composer_id: str) -> list[Path]:
    if not _is_safe_path_segment(composer_id):
        return []
    found: list[Path] = []
    for transcripts_root in _transcript_roots(projects_dir):
        candidate = transcripts_root / composer_id
        if candidate.is_symlink():
            continue
        try:
            candidate.resolve().relative_to(transcripts_root.resolve())
        except (OSError, RuntimeError, ValueError):
            continue
        if candidate.is_dir():
            found.append(candidate)
    return sorted(found)


def nested_transcript_files(projects_dir: Path, composer_id: str) -> list[Path]:
    if not _is_safe_path_segment(composer_id):
        return []
    seen: set[Path] = set()
    found: list[Path] = []
    for transcripts_root in _transcript_roots(projects_dir):
        for parent_dir in transcripts_root.iterdir():
            if parent_dir.is_symlink() or not parent_dir.is_dir():
                continue
            subagents_root = parent_dir / "subagents"
            if subagents_root.is_symlink() or not subagents_root.is_dir():
                continue
            candidate = subagents_root / f"{composer_id}.jsonl"
            if candidate.is_symlink():
                continue
            try:
                resolved = candidate.resolve()
                resolved.relative_to(subagents_root.resolve())
                subagents_root.resolve().relative_to(transcripts_root.resolve())
            except (OSError, RuntimeError, ValueError):
                continue
            if candidate.is_file() and resolved not in seen:
                seen.add(resolved)
                found.append(candidate)
    return sorted(found)


def transcript_dirs_for_ids(projects_dir: Path, composer_ids: list[str]) -> list[Path]:
    seen: set[Path] = set()
    found: list[Path] = []
    for composer_id in composer_ids:
        for path in transcript_dirs(projects_dir, composer_id):
            resolved = path.resolve()
            if resolved in seen or not path.is_dir():
                continue
            seen.add(resolved)
            found.append(path)
    return found


def backup_transcript_dest(
    projects_dir: Path,
    transcripts_root: Path,
    path: Path,
) -> Path:
    try:
        return transcripts_root / path.resolve().relative_to(projects_dir.resolve())
    except ValueError:
        return transcripts_root / path.parent.parent.name / path.name


def directory_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            try:
                total += child.stat().st_size
            except OSError:
                continue
    return total


def make_tree_private(path: Path) -> None:
    path.chmod(0o700)
    for child in path.rglob("*"):
        if child.is_symlink():
            continue
        child.chmod(0o700 if child.is_dir() else 0o600)


def _is_safe_path_segment(value: str) -> bool:
    return bool(
        value
        and value not in {".", ".."}
        and "\x00" not in value
        and Path(value).name == value
    )


def _transcript_roots(projects_dir: Path) -> list[Path]:
    if not projects_dir.is_dir():
        return []
    projects_root = projects_dir.resolve()
    found: list[Path] = []
    for project_dir in projects_dir.iterdir():
        if project_dir.is_symlink():
            continue
        transcripts_root = project_dir / "agent-transcripts"
        if transcripts_root.is_symlink() or not transcripts_root.is_dir():
            continue
        try:
            transcripts_root.resolve().relative_to(projects_root)
        except (OSError, RuntimeError, ValueError):
            continue
        found.append(transcripts_root)
    return sorted(found)
