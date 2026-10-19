import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugin.program.db3roms"
    / "resources"
    / "lib"
    / "storage.py"
)
SPEC = importlib.util.spec_from_file_location("storage", MODULE_PATH)
storage = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(storage)


class StorageHelpersTest(unittest.TestCase):
    def test_system_is_inferred_from_titles_and_filenames(self):
        self.assertEqual(storage.infer_system("Sony PlayStation 2 set"), "PlayStation 2")
        self.assertEqual(storage.infer_system("Game", "release.GBA.zip"), "Game Boy Advance")
        self.assertEqual(storage.infer_system("Unknown collection"), "Other")

    def test_archive_type_and_folder_name(self):
        self.assertEqual(storage.archive_type("Game.ZIP"), "zip")
        self.assertEqual(storage.archive_type("Game.chd"), "")
        self.assertEqual(storage.archive_folder_name("Game (USA).7z"), "Game (USA)")

    def test_game_library_recognises_console_files(self):
        self.assertTrue(storage.is_game_file("Sonic The Hedgehog.md"))
        self.assertTrue(storage.is_game_file("Crisis Core.cso"))
        self.assertTrue(storage.is_game_file("Collection.zip"))
        self.assertFalse(storage.is_game_file("cover.jpg"))
        self.assertFalse(storage.is_game_file("notes.txt"))

    def test_library_paths_can_be_rebased_after_migration(self):
        self.assertEqual(
            storage.rebase_path(
                "/old/roms/Mega Drive/Sonic.md",
                "/old/roms/",
                "/storage/Download/DB3 ROMs/",
            ),
            "/storage/Download/DB3 ROMs/Mega Drive/Sonic.md",
        )
        self.assertEqual(
            storage.rebase_path("/somewhere/game.md", "/old/roms", "/new/roms"),
            "/somewhere/game.md",
        )

    def test_archive_members_cannot_escape_destination(self):
        self.assertEqual(storage.safe_archive_member("folder/game.rom"), "folder/game.rom")
        self.assertEqual(storage.safe_archive_member("../outside.rom"), "")
        self.assertEqual(storage.safe_archive_member("/absolute/game.rom"), "")
        self.assertEqual(storage.safe_archive_member("C:\\outside.rom"), "")

    def test_managed_deletion_requires_a_child_path(self):
        self.assertTrue(storage.is_within("/roms", "/roms/PlayStation/game.zip"))
        self.assertFalse(storage.is_within("/roms", "/roms"))
        self.assertFalse(storage.is_within("/roms", "/other/game.zip"))


if __name__ == "__main__":
    unittest.main()
