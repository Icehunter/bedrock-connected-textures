"""Native connected blocks: neighbour conditions per face, block atlas materials and renderer channels."""
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'converter'))
from native_connected import native_geometry, build_native_ctm, export_material, ROOT

NEIGHBOR_QUERY = re.compile(r"(!?)q\.block_neighbor_has_any_tag\((-?\d+), (-?\d+), (-?\d+), 'bct:test'\)")


class NativeRTXTests(unittest.TestCase):
    def face_cases(self, visibility, face):
        """(tile, [(neighbour offset, must have the tag)]) for each bone of one face."""
        cases = []
        for name, expression in visibility.items():
            if not name.startswith(face + '_'):
                continue
            terms = []
            for term in expression.split(' && '):
                match = NEIGHBOR_QUERY.fullmatch(term)
                self.assertIsNotNone(match)
                terms.append((tuple(map(int, match.group(2, 3, 4))), not bool(match[1])))
            cases.append((int(name.split('_')[-1]), terms))
        return cases

    def test_linear_methods_select_java_end_cases_without_diagonal_dependencies(self):
        from native_connected import FACES, EDGES, VECTORS
        for method, edge_indices in [('horizontal', (3, 1)), ('vertical', (2, 0))]:
            geometry, visibility = native_geometry('geometry.test', 'bct:test', method)
            self.assertEqual(len(geometry['minecraft:geometry'][0]['bones']), 24)
            for face in FACES:
                vectors = [VECTORS[EDGES[face][index]] for index in edge_indices]
                cases = self.face_cases(visibility, face)
                for combination, expected in enumerate((3, 2, 0, 1)):
                    neighbors = {vectors[index] for index in range(2) if combination & (1 << index)}
                    selected = [tile for tile, terms in cases
                                if all((offset in neighbors) == present for offset, present in terms)]
                    self.assertEqual(selected, [expected], (method, face, combination))
        geometry, visibility = native_geometry('geometry.test', 'bct:test', 'fixed')
        self.assertEqual(len(geometry['minecraft:geometry'][0]['bones']), 6)
        self.assertEqual(set(visibility.values()), {'1.0'})

    def test_native_conditions_select_one_java_case_for_every_face_neighborhood(self):
        expected_tiles = json.loads((ROOT / 'tests/fixtures/tile-neighborhoods.json').read_text())['expected']
        geometry, visibility = native_geometry('geometry.test', 'bct:test')
        # Each face's up, right, down and left neighbour, written out independently of the converter's table.
        edges = {'north': [(0, 1, 0), (-1, 0, 0), (0, -1, 0), (1, 0, 0)],
                 'east': [(0, 1, 0), (0, 0, -1), (0, -1, 0), (0, 0, 1)],
                 'south': [(0, 1, 0), (1, 0, 0), (0, -1, 0), (-1, 0, 0)],
                 'west': [(0, 1, 0), (0, 0, 1), (0, -1, 0), (0, 0, -1)],
                 'up': [(0, 0, -1), (1, 0, 0), (0, 0, 1), (-1, 0, 0)],
                 'down': [(0, 0, 1), (1, 0, 0), (0, 0, -1), (-1, 0, 0)]}
        for face, vectors in edges.items():
            cases = self.face_cases(visibility, face)
            for raw in range(256):
                neighbors = {vectors[edge] for edge in range(4) if raw & (1 << edge)}
                for edge in range(4):
                    if raw & (1 << (edge + 4)):
                        following = vectors[(edge + 1) % 4]
                        neighbors.add(tuple(first + second for first, second in zip(vectors[edge], following)))
                selected = [tile for tile, terms in cases
                            if all((offset in neighbors) == present for offset, present in terms)]
                self.assertEqual(selected, [expected_tiles[raw]], (face, raw))
        self.assertEqual(len(geometry['minecraft:geometry'][0]['bones']), 282)

    def test_native_pack_uses_block_atlas_pbr_and_animation_without_entities(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pack = root / 'input'
            pack.mkdir()
            color = Image.new('RGBA', (8, 16), (50, 100, 150, 255))
            color.paste((150, 100, 50, 255), (0, 8, 8, 16))
            color.save(pack / 'tile.png')
            (pack / 'tile.png.mcmeta').write_text(
                json.dumps({'animation': {'frames': [{'index': 1, 'time': 4}, 0], 'frametime': 2}}))
            Image.new('RGB', (8, 16), (128, 128, 255)).save(pack / 'normal.png')
            Image.new('RGBA', (8, 16), (10, 20, 30, 40)).save(pack / 'mers.png')
            texture_set = {'color': 'tile', 'normal': 'normal', 'metalness_emissive_roughness_subsurface': 'mers'}
            (pack / 'tile.texture_set.json').write_text(json.dumps({'minecraft:texture_set': texture_set}))
            rules = root / 'rules.json'
            rule = {'id': 'test', 'method': 'ctm', 'blocks': ['minecraft:stone'], 'tiles': ['tile'] * 47}
            rules.write_text(json.dumps({'format_version': 1, 'rules': [rule]}))
            original = {path.name: path.read_bytes() for path in pack.iterdir()}
            artifact = build_native_ctm(pack, rules, 'test', root=root)
            with zipfile.ZipFile(artifact) as archive:
                names = archive.namelist()
                self.assertFalse(any('/entity/' in name or '/entities/' in name for name in names))
                manifest = json.loads(archive.read('NativeConnected_RP/manifest.json'))
                self.assertIn('raytraced', manifest['capabilities'])
                self.assertEqual(manifest['header']['min_engine_version'], [1, 26, 20])
                block = json.loads(archive.read('NativeConnected_BP/blocks/bct_test_test.json'))['minecraft:block']
                self.assertEqual(len(block['components']['minecraft:material_instances']), 48)
                entry = json.loads(archive.read('NativeConnected_RP/textures/flipbook_textures.json'))[0]
                self.assertEqual(entry['frames'], [0, 0, 1])
                self.assertEqual(entry['ticks_per_frame'], 2)
                for suffix in ('', '_normal', '_mer'):
                    with archive.open('NativeConnected_RP/textures/blocks/bct_test_test_0' + suffix + '.png') as stream:
                        image = Image.open(stream)
                        self.assertEqual(image.size, (8, 16))
                        if not suffix:
                            self.assertEqual(image.getpixel((0, 0)), (150, 100, 50, 255))
                        if suffix == '_mer':
                            self.assertEqual(image.getpixel((0, 0)), (10, 20, 30))
                texture_set_path = 'NativeConnected_RP/textures/blocks/bct_test_test_0.texture_set.json'
                channels = json.loads(archive.read(texture_set_path))['minecraft:texture_set']
                self.assertEqual(channels['normal'], 'bct_test_test_0_normal')
                self.assertNotIn('metalness_emissive_roughness_subsurface', channels)
                self.assertEqual(channels['metalness_emissive_roughness'], 'bct_test_test_0_mer')
            self.assertEqual(original, {path.name: path.read_bytes() for path in pack.iterdir()})
            report = json.loads((root / 'dist/development/native-connected-test-rtx-report.json').read_text())
            self.assertFalse(report['world_blocks_modified'])
            self.assertFalse(report['rtx_verified'])

    def test_renderer_exports_keep_albedo_and_route_material_channels(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source'
            source.mkdir()
            Image.new('RGBA', (8, 8), (70, 80, 90, 255)).save(source / 'tile.png')
            Image.new('RGBA', (8, 8), (20, 30, 40, 50)).save(source / 'mers.png')
            texture_set = {'color': 'tile', 'metalness_emissive_roughness_subsurface': 'mers'}
            (source / 'tile.texture_set.json').write_text(json.dumps({'minecraft:texture_set': texture_set}))
            for mode in ('classic', 'vv', 'rtx'):
                destination = root / mode
                export_material(source, 'tile', destination, 'test', renderer=mode)
                with Image.open(destination / 'textures/blocks/test.png') as color:
                    self.assertEqual(color.getpixel((0, 0)), (70, 80, 90, 255))
                descriptor = destination / 'textures/blocks/test.texture_set.json'
                if mode == 'classic':
                    self.assertFalse(descriptor.exists())
                    continue
                channels = json.loads(descriptor.read_text())['minecraft:texture_set']
                # Vibrant Visuals keeps the subsurface MERS; RTX gets an RGB MER.
                name = 'metalness_emissive_roughness' + ('_subsurface' if mode == 'vv' else '')
                with Image.open(destination / ('textures/blocks/' + channels[name] + '.png')) as material:
                    self.assertEqual(material.getpixel((0, 0)), (20, 30, 40, 50) if mode == 'vv' else (20, 30, 40))


if __name__ == '__main__':
    unittest.main()
