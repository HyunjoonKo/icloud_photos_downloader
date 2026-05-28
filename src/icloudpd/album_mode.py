"""Helpers for mirroring the Photos app folder hierarchy."""

import json
import os
from typing import Dict, Iterable, Sequence, Set, Tuple

ALBUM_MODE_INDEX_FILENAME = ".icloudpd-folder-index.json"
ALBUM_MODE_INDEX_VERSION = 1

AlbumModeIndex = Dict[str, Set[str]]


def _is_safe_relative_path(normalized: str) -> bool:
    """Reject paths that escape the root directory."""
    if os.path.isabs(normalized):
        return False
    if normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        return False
    return True


def _is_absolute_like_path(path: str) -> bool:
    """Reject absolute paths across Windows/POSIX syntaxes before normalization strips roots."""
    normalized = os.path.normpath(path)
    drive, _ = os.path.splitdrive(normalized)
    if drive:
        return True
    return normalized.startswith(("/", "\\"))


def normalize_album_path(path: str) -> str:
    """Normalize a folder/album path for matching and persistence.

    Returns empty string for unsafe paths (absolute or parent-traversal).
    """
    if _is_absolute_like_path(path):
        return ""
    normalized = os.path.normpath(path).replace("\\", "/")
    if normalized == ".":
        return ""
    normalized = normalized.strip("/")
    if not _is_safe_relative_path(normalized):
        return ""
    return normalized


def album_mode_index_path(directory: str) -> str:
    return os.path.join(directory, ALBUM_MODE_INDEX_FILENAME)


def relative_album_mode_path(root_directory: str, full_path: str) -> str:
    return normalize_album_path(os.path.relpath(full_path, root_directory))


def load_album_mode_index(directory: str) -> AlbumModeIndex:
    path = album_mode_index_path(directory)
    try:
        with open(path, encoding="utf-8") as file_obj:
            payload = json.load(file_obj)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return {}

    assets = payload.get("assets", payload) if isinstance(payload, dict) else {}
    if not isinstance(assets, dict):
        return {}

    result: AlbumModeIndex = {}
    for asset_id, paths in assets.items():
        if not isinstance(asset_id, str) or not isinstance(paths, list):
            continue
        normalized_paths = {
            normalize_album_path(path)
            for path in paths
            if isinstance(path, str) and normalize_album_path(path)
        }
        if normalized_paths:
            result[asset_id] = normalized_paths
    return result


def save_album_mode_index(directory: str, index: AlbumModeIndex) -> None:
    path = album_mode_index_path(directory)
    payload = {
        "version": ALBUM_MODE_INDEX_VERSION,
        "assets": {
            asset_id: sorted(normalize_album_path(path) for path in paths if normalize_album_path(path))
            for asset_id, paths in sorted(index.items())
            if paths
        },
    }
    with open(path, "w", encoding="utf-8") as file_obj:
        json.dump(payload, file_obj, ensure_ascii=False, indent=2, sort_keys=True)


def merge_album_mode_index(existing: AlbumModeIndex, updates: AlbumModeIndex) -> AlbumModeIndex:
    merged: AlbumModeIndex = {asset_id: set(paths) for asset_id, paths in existing.items()}
    for asset_id, paths in updates.items():
        if paths:
            merged.setdefault(asset_id, set()).update(paths)
    return merged


def remove_from_album_mode_index(index: AlbumModeIndex, asset_ids: Iterable[str]) -> AlbumModeIndex:
    remaining = {asset_id: set(paths) for asset_id, paths in index.items()}
    for asset_id in asset_ids:
        remaining.pop(asset_id, None)
    return remaining


def select_folder_album_paths(
    requested_paths: Sequence[str], available_paths: Sequence[str]
) -> Tuple[Set[str], Sequence[str]]:
    """Select album paths by exact album path or folder-prefix match."""
    normalized_available = [normalize_album_path(path) for path in available_paths]
    selected: Set[str] = set()
    unmatched: list[str] = []

    for requested in requested_paths:
        normalized_requested = normalize_album_path(requested)
        if not normalized_requested:
            unmatched.append(requested)
            continue

        matches = {
            path
            for path in normalized_available
            if path == normalized_requested or path.startswith(normalized_requested + "/")
        }
        if matches:
            selected.update(matches)
        else:
            unmatched.append(requested)

    return selected, unmatched
