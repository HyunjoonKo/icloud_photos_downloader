import json
import datetime
import logging
import os
import shutil
from types import SimpleNamespace
from unittest import TestCase, mock

import pytest

from icloudpd.album_mode import (
    album_mode_index_path,
    load_album_mode_index,
    merge_album_mode_index,
    normalize_album_path,
    remove_from_album_mode_index,
    save_album_mode_index,
    select_folder_album_paths,
)
from icloudpd.autodelete import autodelete_photos
from pyicloud_ipd.raw_policy import RawTreatmentPolicy
from pyicloud_ipd.version_size import AssetVersionSize
from tests.helpers import path_from_project_root, recreate_path


class FakeMedia:
    def __init__(self, asset_id: str, filename: str) -> None:
        self.id = asset_id
        self.filename = filename
        self.item_type = None
        self.created = datetime.datetime(2024, 1, 1, tzinfo=datetime.UTC)

    def versions_with_raw_policy(self, _raw_policy: RawTreatmentPolicy) -> dict[AssetVersionSize, object]:
        return {AssetVersionSize.ORIGINAL: object()}


class AlbumModeTestCase(TestCase):
    @pytest.fixture(autouse=True)
    def inject_fixtures(self) -> None:
        self.root_path = path_from_project_root(__file__)
        self.fixtures_path = os.path.join(self.root_path, "fixtures")

    def temp_dir(self, test_name: str) -> str:
        base_dir = os.path.join(self.fixtures_path, test_name)
        if os.path.exists(base_dir):
            shutil.rmtree(base_dir)
        recreate_path(base_dir)
        return base_dir

    def test_select_folder_album_paths_supports_exact_album_and_folder_prefix(self) -> None:
        selected, unmatched = select_folder_album_paths(
            ["Trips", "Family/2025/Weekend"],
            [
                "Trips/2024/Seoul",
                "Trips/2024/Tokyo",
                "Family/2025/Weekend",
                "Misc",
            ],
        )

        self.assertEqual(
            selected,
            {
                "Trips/2024/Seoul",
                "Trips/2024/Tokyo",
                "Family/2025/Weekend",
            },
        )
        self.assertEqual(unmatched, [])

    def test_album_mode_index_round_trip_unions_active_asset_paths(self) -> None:
        temp_dir = self.temp_dir("test_album_mode_index_round_trip_unions_active_asset_paths")
        save_album_mode_index(
            temp_dir,
            {
                "asset-a": {"Trips/old.jpg"},
                "asset-b": {"Family/file.jpg"},
            },
        )

        merged_index = merge_album_mode_index(
            load_album_mode_index(temp_dir),
            {
                "asset-a": {"Trips/new.jpg"},
                "asset-c": {"Root/file.mov"},
            },
        )
        save_album_mode_index(temp_dir, merged_index)

        self.assertEqual(
            load_album_mode_index(temp_dir),
            {
                "asset-a": {"Trips/old.jpg", "Trips/new.jpg"},
                "asset-b": {"Family/file.jpg"},
                "asset-c": {"Root/file.mov"},
            },
        )
        self.assertEqual(
            remove_from_album_mode_index(merged_index, ["asset-b"]),
            {
                "asset-a": {"Trips/old.jpg", "Trips/new.jpg"},
                "asset-c": {"Root/file.mov"},
            },
        )

    def test_normalize_album_path_rejects_absolute_and_traversal_paths(self) -> None:
        self.assertEqual(normalize_album_path("/etc/passwd"), "")
        self.assertEqual(normalize_album_path(r"\server\share\file.jpg"), "")
        self.assertEqual(normalize_album_path("C:/temp/file.txt"), "")
        self.assertEqual(normalize_album_path("../../etc/passwd"), "")
        self.assertEqual(normalize_album_path("Trips/2024/IMG_0001.JPG"), "Trips/2024/IMG_0001.JPG")

    def test_load_album_mode_index_filters_absolute_and_escape_paths_from_raw_json(self) -> None:
        temp_dir = self.temp_dir("test_load_album_mode_index_filters_absolute_and_escape_paths_from_raw_json")
        with open(album_mode_index_path(temp_dir), "w", encoding="utf-8") as file_obj:
            json.dump(
                {
                    "version": 1,
                    "assets": {
                        "asset-1": [
                            "/etc/passwd",
                            r"\server\share\file.jpg",
                            "C:/temp/file.txt",
                            "../../escape.txt",
                            "Trips/2024/IMG_0001.JPG",
                        ]
                    },
                },
                file_obj,
            )

        self.assertEqual(
            load_album_mode_index(temp_dir),
            {"asset-1": {"Trips/2024/IMG_0001.JPG"}},
        )

    def test_autodelete_album_mode_uses_index_and_removes_deleted_asset(self) -> None:
        temp_dir = self.temp_dir("test_autodelete_album_mode_uses_index_and_removes_deleted_asset")
        photo_path = os.path.join(temp_dir, "Trips", "2024", "IMG_0001.JPG")
        os.makedirs(os.path.dirname(photo_path), exist_ok=True)
        with open(photo_path, "w", encoding="utf-8"):
            pass

        save_album_mode_index(temp_dir, {"asset-1": {"Trips/2024/IMG_0001.JPG"}})

        media = FakeMedia("asset-1", "IMG_0001.JPG")
        library = SimpleNamespace(recently_deleted=[media])

        with mock.patch("icloudpd.autodelete.disambiguate_filenames") as disambiguate_mock:
            disambiguate_mock.return_value = ({AssetVersionSize.ORIGINAL: object()}, {})
            with mock.patch("icloudpd.autodelete.calculate_version_filename") as filename_mock:
                filename_mock.return_value = "IMG_0001.JPG"
                autodelete_photos(
                    logging.getLogger("album-mode-test"),
                    False,
                    library,
                    "album",
                    temp_dir,
                    [AssetVersionSize.ORIGINAL],
                    lambda filename: filename,
                    RawTreatmentPolicy.AS_IS,
                )

        self.assertFalse(os.path.exists(photo_path))
        self.assertEqual(load_album_mode_index(temp_dir), {})

    def test_autodelete_album_mode_skips_ambiguous_basename_without_index(self) -> None:
        temp_dir = self.temp_dir("test_autodelete_album_mode_skips_ambiguous_basename_without_index")
        first_path = os.path.join(temp_dir, "Trips", "IMG_0001.JPG")
        second_path = os.path.join(temp_dir, "Family", "IMG_0001.JPG")
        os.makedirs(os.path.dirname(first_path), exist_ok=True)
        os.makedirs(os.path.dirname(second_path), exist_ok=True)
        for path in (first_path, second_path):
            with open(path, "w", encoding="utf-8"):
                pass

        media = FakeMedia("asset-1", "IMG_0001.JPG")
        library = SimpleNamespace(recently_deleted=[media])

        with mock.patch("icloudpd.autodelete.disambiguate_filenames") as disambiguate_mock:
            disambiguate_mock.return_value = ({AssetVersionSize.ORIGINAL: object()}, {})
            with mock.patch("icloudpd.autodelete.calculate_version_filename") as filename_mock:
                filename_mock.return_value = "IMG_0001.JPG"
                autodelete_photos(
                    logging.getLogger("album-mode-test"),
                    False,
                    library,
                    "album",
                    temp_dir,
                    [AssetVersionSize.ORIGINAL],
                    lambda filename: filename,
                    RawTreatmentPolicy.AS_IS,
                )

        self.assertTrue(os.path.exists(first_path))
        self.assertTrue(os.path.exists(second_path))

    def test_autodelete_album_mode_rejects_path_traversal_in_index(self) -> None:
        """Index paths with .. traversal must not delete files outside the root."""
        temp_dir = self.temp_dir("test_autodelete_album_mode_rejects_path_traversal_in_index")
        # Create a file outside the managed root
        outside_dir = self.temp_dir("test_autodelete_outside_root")
        outside_file = os.path.join(outside_dir, "important.txt")
        with open(outside_file, "w", encoding="utf-8") as f:
            f.write("do not delete")

        # Craft a malicious index with a path that escapes the root
        relative_escape = os.path.relpath(outside_file, temp_dir).replace("\\", "/")
        with open(album_mode_index_path(temp_dir), "w", encoding="utf-8") as file_obj:
            json.dump({"version": 1, "assets": {"asset-evil": [relative_escape]}}, file_obj)

        media = FakeMedia("asset-evil", "important.txt")
        library = SimpleNamespace(recently_deleted=[media])

        with mock.patch("icloudpd.autodelete.disambiguate_filenames") as disambiguate_mock:
            disambiguate_mock.return_value = ({AssetVersionSize.ORIGINAL: object()}, {})
            with mock.patch("icloudpd.autodelete.calculate_version_filename") as filename_mock:
                filename_mock.return_value = "important.txt"
                autodelete_photos(
                    logging.getLogger("album-mode-test"),
                    False,
                    library,
                    "album",
                    temp_dir,
                    [AssetVersionSize.ORIGINAL],
                    lambda filename: filename,
                    RawTreatmentPolicy.AS_IS,
                )

        # File outside root must survive
        self.assertTrue(os.path.exists(outside_file))

    def test_autodelete_album_mode_fallback_resolves_after_indexed_delete(self) -> None:
        """After an indexed delete removes a file, fallback should not treat the same
        basename as ambiguous for a later unindexed asset."""
        temp_dir = self.temp_dir("test_autodelete_album_mode_fallback_resolves")
        # Two files with the same basename in different folders
        indexed_path = os.path.join(temp_dir, "Trips", "IMG_0001.JPG")
        unindexed_path = os.path.join(temp_dir, "Family", "IMG_0001.JPG")
        os.makedirs(os.path.dirname(indexed_path), exist_ok=True)
        os.makedirs(os.path.dirname(unindexed_path), exist_ok=True)
        for p in (indexed_path, unindexed_path):
            with open(p, "w", encoding="utf-8"):
                pass

        # Only asset-1 is indexed; asset-2 is not
        save_album_mode_index(temp_dir, {"asset-1": {"Trips/IMG_0001.JPG"}})

        media_indexed = FakeMedia("asset-1", "IMG_0001.JPG")
        media_unindexed = FakeMedia("asset-2", "IMG_0001.JPG")
        library = SimpleNamespace(recently_deleted=[media_indexed, media_unindexed])

        with mock.patch("icloudpd.autodelete.disambiguate_filenames") as disambiguate_mock:
            disambiguate_mock.return_value = ({AssetVersionSize.ORIGINAL: object()}, {})
            with mock.patch("icloudpd.autodelete.calculate_version_filename") as filename_mock:
                filename_mock.return_value = "IMG_0001.JPG"
                autodelete_photos(
                    logging.getLogger("album-mode-test"),
                    False,
                    library,
                    "album",
                    temp_dir,
                    [AssetVersionSize.ORIGINAL],
                    lambda filename: filename,
                    RawTreatmentPolicy.AS_IS,
                )

        # Both files should be deleted: asset-1 via index, asset-2 via fallback (no longer ambiguous)
        self.assertFalse(os.path.exists(indexed_path))
        self.assertFalse(os.path.exists(unindexed_path))
