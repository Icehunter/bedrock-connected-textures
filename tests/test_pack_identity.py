"""A converted pack keeps the author's description, credit and version."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from pack_identity import (identity, manifest_description, pack_title, parse_version, plain_text, reference_version,
                           resource_formats, version_from_name)


def mcmeta(pack):
    return json.dumps({'pack': pack}).encode()


class PackIdentityTests(unittest.TestCase):
    def test_description_and_credit_come_from_pack_mcmeta(self):
        pack = {'description': 'Textures made for shaders \nBy Some Artist', 'min_format': [97.1], 'max_format': [97.1]}
        found = identity(mcmeta(pack), 'Some_Pack_26.3_64x_basic.zip')
        self.assertEqual(found['description'], 'Textures made for shaders \nBy Some Artist')
        self.assertEqual(found['authors'], ['Some Artist'])
        self.assertEqual(found['version'], [1, 0, 0], 'the game version in the name is not a pack version')
        self.assertEqual(found['formats'], ((97, 1), (97, 1)))
        self.assertEqual(manifest_description(found), 'Textures made for shaders \nBy Some Artist',
                         "the author's words, unchanged")
        self.assertEqual(pack_title('Some_Pack R4.1.0 256x.zip'), 'Some Pack R4.1.0 256x')

    def test_versions_come_from_tags_in_the_name_or_the_option(self):
        self.assertEqual(version_from_name('Some Pack R4.1.0 256x.zip'), [4, 1, 0])
        self.assertEqual(version_from_name('pack-v2.3.zip'), [2, 3, 0])
        self.assertIsNone(version_from_name('Pack_26.2_64x.zip'))
        self.assertIsNone(version_from_name('river_pack.zip'), 'letters inside a word are not a tag')
        self.assertEqual(identity(None, 'x.zip', version='5.2')['version'], [5, 2, 0])
        self.assertEqual(parse_version('R4.1.0'), [4, 1, 0])
        with self.assertRaises(ValueError):
            parse_version('four')

    def test_text_components_and_formatting_codes(self):
        self.assertEqual(plain_text([{'text': '§6Gold ', 'extra': [{'text': 'pack'}]}, ' by Me']), 'Gold pack by Me')

    def test_declared_formats(self):
        pack = {'pack_format': 80, 'supported_formats': [15, 64], 'min_format': [15, 0], 'max_format': [130, 114514]}
        self.assertEqual(resource_formats(pack), ((15, 0), (130, 114514)))
        self.assertEqual(resource_formats({'pack_format': 88}), ((88, 0), (88, 0)))
        self.assertIsNone(resource_formats({}))

    def test_reference_release_follows_the_declared_format(self):
        releases = [{'version': '26.2', 'resource': [88, 0]}, {'version': '26.3', 'resource': [97, 1]}]
        self.assertEqual(reference_version(((88, 0), (88, 0)), releases), '26.2')
        self.assertEqual(reference_version(((97, 1), (97, 1)), releases), '26.3')
        self.assertEqual(reference_version(((15, 0), (130, 114514)), releases), '26.3', 'a wide range takes the newest')
        self.assertEqual(reference_version(((90, 0), (95, 0)), releases), '26.2',
                         'between releases: the newest older one')
        self.assertEqual(reference_version(((10, 0), (20, 0)), releases), '26.2', 'older than all: the oldest known')
        self.assertEqual(reference_version(None, releases), '26.3')


if __name__ == '__main__':
    unittest.main()
