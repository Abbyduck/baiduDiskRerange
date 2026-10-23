import tempfile
import unittest
from pathlib import Path

from project_paths import resolve_data_dir


class ProjectPathTests(unittest.TestCase):
    def test_new_checkout_keeps_private_data_beneath_local_data(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory).resolve()
            self.assertEqual(resolve_data_dir(root, {}), root / 'local-data/inventory')

    def test_existing_legacy_data_is_not_silently_abandoned(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory).resolve()
            legacy = root / '盘点_2026-10-20'
            legacy.mkdir()
            self.assertEqual(resolve_data_dir(root, {}), legacy)
            (root / 'local-data/inventory').mkdir(parents=True)
            self.assertEqual(resolve_data_dir(root, {}), root / 'local-data/inventory')

    def test_relative_override_is_relative_to_project_not_shell(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory).resolve()
            self.assertEqual(resolve_data_dir(root, {'BAIDU_NETDISK_DATA_DIR': 'custom/data'}),
                             root / 'custom/data')

    def test_absolute_override_takes_precedence(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            root = Path(directory).resolve()
            (root / 'local-data/inventory').mkdir(parents=True)
            target = root / 'other'
            self.assertEqual(resolve_data_dir(root, {'BAIDU_NETDISK_DATA_DIR': str(target)}), target)


if __name__ == '__main__':
    unittest.main()
