import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[1]
    / "plugin.program.db3roms"
    / "resources"
    / "lib"
    / "ia_client.py"
)
SPEC = importlib.util.spec_from_file_location("ia_client", MODULE_PATH)
ia = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ia)


class InternetArchiveHelpersTest(unittest.TestCase):
    def test_search_url_contains_repeated_fields_and_query(self):
        url = ia.build_search_url(ia.homebrew_query(), page=2, rows=10)
        self.assertIn("advancedsearch.php", url)
        self.assertIn("page=2", url)
        self.assertIn("rows=10", url)
        self.assertIn("fl%5B%5D=identifier", url)

    def test_file_filter_only_returns_rom_candidates(self):
        files = [
            {"name": "game.nes", "size": "123"},
            {"name": "manual.pdf", "size": "456"},
            {"name": "item_meta.sqlite", "size": "789"},
            {"name": "covers/", "size": "0"},
            {"name": "private.zip", "private": True},
            {"name": "bundle.zip", "size": "2048"},
        ]
        result = ia.filter_rom_files(files, (".nes", ".zip"))
        self.assertEqual(
            [entry["name"] for entry in result], ["bundle.zip", "game.nes"]
        )

    def test_private_files_require_an_authenticated_session(self):
        files = [{"name": "account-only.zip", "private": True}]
        self.assertEqual(ia.filter_rom_files(files), [])
        self.assertEqual(
            [entry["name"] for entry in ia.filter_rom_files(files, allow_private=True)],
            ["account-only.zip"],
        )

    def test_download_url_escapes_names_but_preserves_subfolders(self):
        url = ia.download_url("item name", "folder/My Game (USA).zip")
        self.assertIn("item%20name", url)
        self.assertIn("folder/My%20Game%20%28USA%29.zip", url)

    def test_html_and_sizes_are_cleaned(self):
        self.assertEqual(ia.strip_html("<p>Hello <b>world</b></p>"), "Hello world")
        self.assertEqual(ia.format_size(1048576), "1.0 MB")

    def test_hidden_login_fields_are_preserved(self):
        html = (
            '<form><input value="token-value" name="csrf" type="hidden">'
            '<input type="hidden" name="referer" value="/details/test">'
            '<input type="text" name="username"></form>'
        )
        self.assertEqual(
            ia.hidden_form_fields(html),
            {"csrf": "token-value", "referer": "/details/test"},
        )


if __name__ == "__main__":
    unittest.main()
