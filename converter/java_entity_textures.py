"""Map a Java pack's entity textures onto the Bedrock entity textures that draw them.

A Java texture replaces a Bedrock texture only on evidence: the table in
data/java-entity-textures.json lists Bedrock textures whose vanilla pixels
agree with the vanilla Java texture inside the UV rectangles the Bedrock
geometry samples (UV coordinates are normalised, so the author's resolution
and aspect are kept). Textures whose layout differs get a rectangle remap when
the face correspondence is known and is checked against the vanilla pair at
conversion time (chests, the 64x32 zombie and husk, the stacked sheep);
everything else is reported with a reason.

Bedrock materials that read texture alpha as an emission or tint mask (spider
eyes, glow squid, sheep wool, leather armour...) keep the vanilla Bedrock alpha
codes under the author's colours; eye layers become alpha-as-emissive like the
vanilla Bedrock textures. LabPBR ``_n``/``_s`` maps become Bedrock texture-set
normal and MERS maps for Vibrant Visuals.

TexturePlanner decides what every authored texture becomes; build_output then
draws one planned Bedrock texture with its PBR maps.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from common import read_json
from java_entity_models import box_uv_faces
from java_materials import MaterialPolicy, decode_labpbr, source_image

JAVA_TEXTURES = 'assets/minecraft/textures/'
JAVA_ENTITY_TEXTURES = JAVA_TEXTURES + 'entity/'
TEXTURES_TABLE = Path(__file__).with_name('data') / 'java-entity-textures.json'
# LabPBR 1.3 maps, DirectX normal Y, perceptual roughness; channels Bedrock cannot show are reported, not fatal.
LABPBR_POLICY = MaterialPolicy('labpbr-1.3', 'directx', 'perceptual', True)
# Share of the vanilla UV islands an author's texture must fill to count as drawn on the vanilla layout.
MIN_ISLANDS_FILLED = 0.5
# An older Java path with no vanilla Java texture to compare must fill this much of the Bedrock layout.
MIN_LEGACY_ISLANDS_FILLED = 0.9
# A vanilla texture less opaque than this is a sparse overlay (cracks, markings), whose islands are artwork.
SPARSE_OVERLAY_OPACITY = 0.15
# Java eye layer -> Java bodies it is drawn over (vanilla renderers).
EYE_LAYERS = {
    'entity/spider/spider_eyes': ('entity/spider/spider', 'entity/spider/cave_spider'),
    'entity/enderman/enderman_eyes': ('entity/enderman/enderman',),
    'entity/phantom/phantom_eyes': ('entity/phantom/phantom',),
}
# Vanilla Bedrock eye alpha when the target texture has none to copy.
DEFAULT_EYE_ALPHA = 3
# LabPBR keeps emission in the specular alpha: 254 is full emission and 255 none.
FULL_EMISSION = 254
NO_EMISSION = 255
# Evidence ranks: a plan for a Bedrock texture replaces one of the same or a lower rank.
RANK_LEGACY_PATH = 1
RANK_SAME_LAYOUT = 2
RANK_MODEL_TEXTURE = 3

# Each Bedrock chest face -> (the Java chest face whose pixels it shows, orientation). The Java 1.15+
# chest model is the old one turned half a turn about X. Orientations: 'id' as is, 'fx'/'fy' flipped
# horizontally/vertically, 'r180' turned half a turn.
FACE_TURN = {'up': ('down', 'fy'), 'down': ('up', 'fy'), 'north': ('south', 'r180'),
             'south': ('north', 'r180'), 'east': ('east', 'r180'), 'west': ('west', 'r180')}
# (box, texture offset, size) of the chest boxes, laid out with box UV.
SINGLE_CHEST_BOXES = (('lid', (0, 0), (14, 5, 14)), ('base', (0, 19), (14, 10, 14)), ('lock', (0, 0), (2, 4, 1)))
DOUBLE_CHEST_BOXES = (('lid', (0, 0), (30, 5, 14)), ('base', (0, 19), (30, 10, 14)), ('lock', (0, 0), (2, 4, 1)))
HALF_CHEST_BOXES = (('lid', (0, 0), (15, 5, 14)), ('base', (0, 19), (15, 10, 14)), ('lock', (0, 0), (1, 4, 1)))
CHEST_FAMILIES = (
    # Java single, Java left, Java right, Bedrock single, Bedrock double
    ('entity/chest/normal', 'entity/chest/normal_left', 'entity/chest/normal_right',
     'textures/entity/chest/normal', 'textures/entity/chest/double_normal'),
    ('entity/chest/trapped', 'entity/chest/trapped_left', 'entity/chest/trapped_right',
     'textures/entity/chest/trapped', 'textures/entity/chest/trapped_double'),
    ('entity/chest/ender', None, None, 'textures/entity/chest/ender', None),
    ('entity/chest/copper', 'entity/chest/copper_left', 'entity/chest/copper_right',
     'textures/entity/chest/copper_default', 'textures/entity/chest/copper_default_double'),
    ('entity/chest/copper_exposed', 'entity/chest/copper_exposed_left', 'entity/chest/copper_exposed_right',
     'textures/entity/chest/copper_exposed', 'textures/entity/chest/copper_exposed_double'),
    ('entity/chest/copper_weathered', 'entity/chest/copper_weathered_left', 'entity/chest/copper_weathered_right',
     'textures/entity/chest/copper_weathered', 'textures/entity/chest/copper_weathered_double'),
    ('entity/chest/copper_oxidized', 'entity/chest/copper_oxidized_left', 'entity/chest/copper_oxidized_right',
     'textures/entity/chest/copper_oxidized', 'textures/entity/chest/copper_oxidized_double'),
)
# 64x64 humanoid textures whose Bedrock counterpart is the 64x32 top half.
TOP_HALF_TEXTURES = (('entity/zombie/zombie', 'textures/entity/zombie/zombie'),
                     ('entity/zombie/husk', 'textures/entity/zombie/husk'))
# Bedrock sheep: body above, wool below, one texture.
STACKED_SHEEP = (('entity/sheep/sheep', 'entity/sheep/sheep_wool', 'textures/entity/sheep/sheep'),)


def load_table(path=TEXTURES_TABLE):
    return read_json(Path(path))


def emissive_suffix(stack):
    """OptiFine emissive overlay suffix (optifine/emissive.properties, '_e' by default)."""
    path = 'assets/minecraft/optifine/emissive.properties'
    if path in getattr(stack, 'files', {}):
        for line in stack.read(path).decode('utf-8', 'replace').splitlines():
            key, _, value = line.partition('=')
            if key.strip() == 'suffix.emissive' and value.strip():
                return value.strip()
    return '_e'


def decode(data):
    image, _ = source_image(data)
    return image


def bedrock_file(samples, relative):
    """The vanilla Bedrock texture file for a texture path without extension (.tga first), or None."""
    for extension in ('.tga', '.png'):
        path = Path(samples) / (relative + extension)
        if path.is_file():
            return path
    return None


def encode(image, extension):
    """Image bytes in the format of the extension (.tga run-length encoded, else PNG)."""
    raw = io.BytesIO()
    if extension == '.tga':
        image.convert('RGBA').save(raw, format='TGA', compression='tga_rle')
    else:
        image.save(raw, format='PNG')
    return raw.getvalue()


def layout_coverage(author, vanilla):
    """Share of the vanilla texture's opaque UV islands that the author's texture also fills.

    Authors often pad islands outwards, so only missing islands count against a
    texture. Returns None for sparse overlays (cracks, markings), where island
    shapes are artwork rather than layout.
    """
    size = (min(author.width, vanilla.width), min(author.height, vanilla.height))
    reference = _rgba_array(_resized(vanilla, size))[..., 3] > 0
    if reference.mean() < SPARSE_OVERLAY_OPACITY:
        return None
    drawn = _rgba_array(_resized(author, size))[..., 3] > 0
    return float((reference & drawn).sum() / max(reference.sum(), 1))


def alpha_codes(image):
    """Alpha values other than fully transparent or opaque: the material reads alpha as a mask."""
    values = set(np.unique(_rgba_array(image)[..., 3]).tolist())
    return sorted(values - {0, 255})


def resized_mask(mask, size):
    """A boolean mask scaled to size (width, height) with nearest-neighbour sampling."""
    return np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize(size, Image.Resampling.NEAREST)) > 0


def _rgba_array(image):
    return np.asarray(image.convert('RGBA'))


def _resized(image, size):
    if image.size == tuple(size):
        return image
    return image.resize(tuple(size), Image.Resampling.NEAREST)


# --- rectangle remaps ---

def single_chest_rects():
    """Java 1.15+ chest faces (the old model turned half a turn about X) -> Bedrock chest faces."""
    faces = _box_face_rects(SINGLE_CHEST_BOXES)
    rects = []
    for box in faces:
        for face, target in faces[box].items():
            source_face, orientation = FACE_TURN[face]
            rects.append(['java', faces[box][source_face], target, orientation])
    return rects


def double_chest_rects():
    """Java left/right chest halves -> one Bedrock double chest texture."""
    bedrock = _box_face_rects(DOUBLE_CHEST_BOXES)
    half = _box_face_rects(HALF_CHEST_BOXES)
    rects = []
    for box in bedrock:
        for face, target in bedrock[box].items():
            if face in ('east', 'west'):
                # Each end of the double chest is the outer side of one half.
                side = 'right' if face == 'east' else 'left'
                rects.append([side, half[box][face], target, 'r180'])
                continue
            source_face, orientation = FACE_TURN[face]
            # A face across both halves takes one half of its width from each, in the order seen on that face.
            order = ('left', 'right') if face == 'south' else ('right', 'left')
            width = target[2] / 2.0
            for index, side in enumerate(order):
                part = [target[0] + index * width, target[1], width, target[3]]
                rects.append([side, half[box][source_face], part, orientation])
    return rects


def apply_rects(sources, uv_sizes, rects, target_uv, *, normal=False, fill=(0, 0, 0, 0)):
    """Paste UV rectangles of named source images into a new texture.

    rects are [source name, source [x, y, w, h], target [x, y, w, h], orientation]
    in UV units. The output keeps the finest source pixel density; normal also
    flips the vectors of a normal map with its pixels.
    """
    density = max(max(sources[name].width / uv_sizes[name][0], sources[name].height / uv_sizes[name][1])
                  for name in sources)
    size = (max(1, round(target_uv[0] * density)), max(1, round(target_uv[1] * density)))
    canvas = np.zeros((size[1], size[0], 4), dtype=np.uint8)
    canvas[...] = fill
    for name, source_rect, target_rect, orientation in rects:
        image = sources[name]
        crop_box = _pixel_box(source_rect, image.width / uv_sizes[name][0], image.height / uv_sizes[name][1])
        if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
            continue
        piece = _oriented(_rgba_array(image.crop(crop_box)), orientation)
        if normal:
            piece = _flipped_normal_vectors(piece, orientation)
        paste_box = _pixel_box(target_rect, density, density)
        if paste_box[2] <= paste_box[0] or paste_box[3] <= paste_box[1]:
            continue
        paste_size = (paste_box[2] - paste_box[0], paste_box[3] - paste_box[1])
        piece = np.asarray(Image.fromarray(np.ascontiguousarray(piece)).resize(paste_size, Image.Resampling.NEAREST))
        canvas[paste_box[1]:paste_box[3], paste_box[0]:paste_box[2]] = piece
    return Image.fromarray(canvas)


def remap_agreement(java_images, uv_sizes, rects, bedrock_image, target_uv, sampled=None):
    """Apply a remap to vanilla Java textures and compare with vanilla Bedrock inside the mapped rectangles.

    sampled, when given, limits the comparison to the texels the Bedrock geometry reads.
    Returns (colour agreement, alpha agreement) as shares of the used pixels.
    """
    built = _rgba_array(apply_rects(java_images, uv_sizes, rects, target_uv))
    height, width = built.shape[:2]
    expected = _rgba_array(_resized(bedrock_image, (width, height)))
    compared = np.zeros((height, width), bool)
    density = width / target_uv[0]
    for _, _, (x, y, rect_width, rect_height), _ in rects:
        compared[round(y * density):round((y + rect_height) * density),
                 round(x * density):round((x + rect_width) * density)] = True
    if sampled is not None:
        compared &= resized_mask(sampled, (width, height))
    used = compared & ((built[..., 3] > 0) | (expected[..., 3] > 0))
    if not used.any():
        return 0.0, 0.0
    alpha_agrees = used & ((built[..., 3] > 0) == (expected[..., 3] > 0))
    # Colours within 8 levels on every channel count as the same.
    colour_distance = np.abs(built[..., :3].astype(int) - expected[..., :3].astype(int)).max(axis=2)
    colour_agrees = alpha_agrees & (colour_distance <= 8)
    return float(colour_agrees.sum() / used.sum()), float(alpha_agrees.sum() / used.sum())


def remap_accepted(colour, alpha):
    """Identical vanilla pixels, or the same layout with redrawn vanilla art."""
    return colour >= 0.97 or (alpha >= 0.98 and colour >= 0.6)


def remap_recipes():
    """Every rectangle remap: Bedrock target -> recipe.

    A recipe names its Java sources by role, gives their UV sizes, the rectangles
    and the target UV size; the stacked sheep also gives each role the alpha Bedrock reads.
    """
    recipes = {}
    single, double = single_chest_rects(), double_chest_rects()
    for java, left, right, bedrock_single, bedrock_double in CHEST_FAMILIES:
        recipes[bedrock_single] = {'sources': {'java': java}, 'uv': {'java': (64, 64)}, 'rects': single,
                                   'target_uv': (64, 64)}
        if left and bedrock_double:
            recipes[bedrock_double] = {'sources': {'left': left, 'right': right},
                                       'uv': {'left': (64, 64), 'right': (64, 64)},
                                       'rects': double, 'target_uv': (128, 64)}
    for java, bedrock in TOP_HALF_TEXTURES:
        recipes[bedrock] = {'sources': {'java': java}, 'uv': {'java': (64, 64)},
                            'rects': [['java', [0, 0, 64, 32], [0, 0, 64, 32], 'id']], 'target_uv': (64, 32)}
    for body, wool, bedrock in STACKED_SHEEP:
        # Bedrock tints opaque sheep pixels with the wool colour except those with alpha 3: the Java
        # body is drawn untinted and its wool layer tinted.
        recipes[bedrock] = {'sources': {'body': body, 'wool': wool}, 'uv': {'body': (64, 32), 'wool': (64, 32)},
                            'rects': [['body', [0, 0, 64, 32], [0, 0, 64, 32], 'id'],
                                      ['wool', [0, 0, 64, 32], [0, 32, 64, 32], 'id']],
                            'target_uv': (64, 64), 'role_alpha': {'body': 3, 'wool': 255}}
    return recipes


def _box_face_rects(boxes):
    """Box name -> face -> [x, y, w, h] of every box in a box-UV layout."""
    return {name: {face: _xywh(rect) for face, rect in box_uv_faces(offset, size).items()}
            for name, offset, size in boxes}


def _xywh(rect):
    """A [u1, v1, u2, v2] rectangle (corners in any order) as [x, y, width, height]."""
    x1, y1, x2, y2 = rect
    return [min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1)]


def _pixel_box(rect, scale_x, scale_y):
    """Pixel crop box (left, top, right, bottom) of an [x, y, w, h] UV rectangle."""
    x, y, width, height = rect
    return (round(x * scale_x), round(y * scale_y), round((x + width) * scale_x), round((y + height) * scale_y))


def _oriented(array, orientation):
    if orientation == 'fx':
        return array[:, ::-1]
    if orientation == 'fy':
        return array[::-1, :]
    if orientation == 'r180':
        return array[::-1, ::-1]
    return array


def _flipped_normal_vectors(array, orientation):
    """Flipping a tangent-space normal map also flips its vectors (LabPBR: R=x, G=y)."""
    array = array.copy()
    if orientation in ('fx', 'r180'):
        array[..., 0] = 255 - array[..., 0]
    if orientation in ('fy', 'r180'):
        array[..., 1] = 255 - array[..., 1]
    return array


# --- planning ---

class TexturePlanner:
    """Decides which Bedrock textures a pack's entity textures become, with the evidence for each.

    records lists every authored entity texture with its status and reason;
    outputs maps every Bedrock texture to write to the recipe that builds it.
    """

    def __init__(self, stack, jar, samples, table=None, sampled=None):
        # sampled(target) is the UV mask the Bedrock geometry reads from a texture, or None.
        self.sampled = sampled or (lambda target: None)
        self.stack = stack
        self.jar = jar
        self.jar_names = set(jar.namelist())
        self.samples = Path(samples)
        self.table = table or load_table()
        self.records = []
        self.outputs = {}
        self.coupled_sources = set()
        self._vanilla_cache = {}
        self.emissive_suffix = emissive_suffix(stack)

    def authored_textures(self):
        """The pack's entity textures, leaving out the LabPBR maps and emissive overlays of another texture."""
        found = []
        emissive_ending = self.emissive_suffix + '.png'
        for path in sorted(self.stack.files):
            if not path.startswith(JAVA_ENTITY_TEXTURES) or not path.endswith('.png'):
                continue
            if path.endswith(('_n.png', '_s.png')) and path[:-6] + '.png' in self.stack.files:
                continue
            if path.endswith(emissive_ending) and path[:-len(emissive_ending)] + '.png' in self.stack.files:
                continue
            found.append(path)
        return found

    def java_identity(self, path):
        """(identity, path kind) of an authored texture path.

        The identity is the texture's current Java name: the path itself when the Java
        jar has it ('current'), the newer name of an older path ('legacy'), or the path
        itself when the table maps it straight to Bedrock ('legacy_target').
        """
        relative = path[len(JAVA_TEXTURES):-4]
        if JAVA_TEXTURES + relative + '.png' in self.jar_names:
            return relative, 'current'
        legacy_paths = self.table.get('legacy_paths', {})
        if relative in legacy_paths:
            return legacy_paths[relative], 'legacy'
        if relative in self.table.get('legacy_targets', {}):
            return relative, 'legacy_target'
        return None, 'unknown'

    def vanilla_java(self, identity):
        path = JAVA_TEXTURES + identity + '.png'
        if path not in self.jar_names:
            return None
        return decode(self.jar.read(path))

    def vanilla_bedrock(self, target):
        """(vanilla Bedrock image, file extension) of a Bedrock texture path, or (None, None)."""
        if target not in self._vanilla_cache:
            file = bedrock_file(self.samples, target)
            if file is None:
                self._vanilla_cache[target] = (None, None)
            else:
                with Image.open(file) as image:
                    self._vanilla_cache[target] = (image.convert('RGBA'), file.suffix.lower())
        return self._vanilla_cache[target]

    def authored_image(self, path):
        return decode(self.stack.read(path))

    def plan(self, coupled=None):
        """Plan every output; returns self.

        coupled maps each Bedrock texture a converted entity model draws to the author's
        texture for it: {'source', 'reference'}, or just the source path.
        """
        coupled = {target: (value if isinstance(value, dict) else {'source': value, 'reference': target})
                   for target, value in (coupled or {}).items()}
        self.coupled_sources = {value['source'] for value in coupled.values()}
        authored = self.authored_textures()
        recipes = remap_recipes()
        remap_targets = {}
        for target, recipe in recipes.items():
            for identity in recipe['sources'].values():
                remap_targets.setdefault(identity, []).append(target)
        identities = {path: self.java_identity(path) for path in authored}
        chosen_paths = _chosen_paths(identities)
        for path in authored:
            identity, path_kind = identities[path]
            self._plan_authored(path, identity, path_kind, chosen_paths, remap_targets, coupled)
        self._plan_remaps(recipes, chosen_paths)
        self._plan_model_textures(coupled)
        self._plan_eyes(chosen_paths)
        return self

    def _plan_authored(self, path, identity, path_kind, chosen_paths, remap_targets, coupled):
        """Record one authored texture and plan what it becomes (remaps and eyes are planned later)."""
        record = {'source': path, 'identity': identity, 'path_kind': path_kind, 'targets': []}
        self.records.append(record)
        if path in self.coupled_sources:
            record.update(status='model_texture', reason='drawn by the converted entity model')
            # Other Bedrock textures sharing the vanilla layout (a mob head, say) still take the art
            # when the texture keeps the vanilla layout.
            if identity and chosen_paths.get(identity) == path and identity not in EYE_LAYERS:
                self._plan_direct(path, identity, record, skip=set(coupled), quiet=True)
            return
        if identity is None:
            record.update(status='unused', reason='no Java version reads this path')
        elif chosen_paths.get(identity) != path:
            record.update(status='superseded', reason='the pack also ships ' + chosen_paths[identity])
        elif identity in EYE_LAYERS:
            record.update(status='merged', reason='eye layer becomes alpha-as-emissive of the body texture')
        elif identity in remap_targets:
            record.update(status='remap_source', targets=remap_targets[identity])
        elif path_kind == 'legacy_target':
            self._plan_legacy_target(path, record)
        else:
            self._plan_direct(path, identity, record)

    def _plan_direct(self, path, identity, record, *, skip=(), quiet=False):
        """Plan the texture for every Bedrock texture with its vanilla layout.

        quiet: the texture already has its status (a model texture), so only targets are added.
        """
        targets = [(target, score) for target, score in self.table['direct'].get(identity, []) if target not in skip]
        if not targets:
            if not quiet:
                record.update(status='unsupported',
                              reason='no Bedrock texture shares the vanilla layout of ' + identity)
            return
        vanilla = self.vanilla_java(identity)
        author = self.authored_image(path)
        coverage = layout_coverage(author, vanilla) if vanilla is not None else None
        record['vanilla_islands_filled'] = None if coverage is None else round(coverage, 3)
        if coverage is not None and coverage < MIN_ISLANDS_FILLED:
            if not quiet:
                record.update(status='unsupported',
                              reason='the texture leaves most vanilla UV islands empty (%.2f filled); '
                                     'it was drawn for a custom model the pack does not include' % coverage)
            return
        written = []
        for target, score in targets:
            evidence = {'kind': 'vanilla_pair', 'score': score,
                        'vanilla_islands_filled': record['vanilla_islands_filled'], 'rank': RANK_SAME_LAYOUT}
            if self._add_output(target, {'kind': 'direct', 'source': path}, [path], evidence):
                written.append(target)
        if quiet:
            record['targets'] = sorted(set(record.get('targets', [])) | set(written))
            return
        record.update(status='converted' if written else 'unsupported', targets=written,
                      reason=None if written else 'the Bedrock texture is missing from the reference')

    def _plan_legacy_target(self, path, record):
        """An older Java path that the table maps to Bedrock textures, matched by layout alone."""
        written = []
        author = self.authored_image(path)
        for target in self.table['legacy_targets'][record['identity']]:
            vanilla, _ = self.vanilla_bedrock(target)
            if vanilla is None:
                continue
            coverage = layout_coverage(author, vanilla)
            if coverage is not None and coverage < MIN_LEGACY_ISLANDS_FILLED:
                rejected = {'target': target, 'vanilla_islands_filled': round(coverage, 3)}
                record.setdefault('rejected', []).append(rejected)
                continue
            evidence = {'kind': 'legacy_java_path',
                        'bedrock_islands_filled': None if coverage is None else round(coverage, 3),
                        'rank': RANK_LEGACY_PATH}
            if self._add_output(target, {'kind': 'direct', 'source': path}, [path], evidence):
                written.append(target)
        if written:
            reason = 'older Java path, matched to the Bedrock texture by layout'
        else:
            reason = 'older Java path whose layout does not match the Bedrock texture'
        record.update(status='converted' if written else 'unsupported', targets=written, reason=reason)

    def _plan_remaps(self, recipes, chosen_paths):
        """Plan the rectangle remaps of the textures the pack ships, once the vanilla pair confirms each."""
        for target, recipe in recipes.items():
            roles = {role: chosen_paths.get(identity) for role, identity in recipe['sources'].items()}
            if not any(roles.values()):
                continue
            vanilla_bedrock, _ = self.vanilla_bedrock(target)
            vanilla_java = {role: self.vanilla_java(identity) for role, identity in recipe['sources'].items()}
            if vanilla_bedrock is None or any(image is None for image in vanilla_java.values()):
                continue
            colour, alpha = remap_agreement(vanilla_java, recipe['uv'], recipe['rects'], vanilla_bedrock,
                                            recipe['target_uv'], self.sampled(target))
            authored_paths = [path for path in roles.values() if path]
            if not remap_accepted(colour, alpha):
                reason = ('remap does not reproduce the vanilla Bedrock layout (colour %.2f, alpha %.2f)'
                          % (colour, alpha))
                for path in authored_paths:
                    self._mark_source(path, 'unsupported', reason)
                continue
            missing = [role for role, path in roles.items() if path is None]
            recipe_fields = {key: recipe[key] for key in ('uv', 'rects', 'target_uv', 'role_alpha') if key in recipe}
            evidence = {'kind': 'remap', 'vanilla_colour_agreement': round(colour, 4),
                        'vanilla_alpha_agreement': round(alpha, 4), 'rank': RANK_SAME_LAYOUT}
            output = self._add_output(target, {'kind': 'remap', 'roles': roles, 'missing': missing, **recipe_fields},
                                      authored_paths, evidence)
            if output:
                if recipe.get('role_alpha'):
                    output['alpha_codes'] = 'by_role'
                for path in authored_paths:
                    self._mark_source(path, 'remapped', None, target)

    def _plan_model_textures(self, coupled):
        """Plan the textures converted entity models draw, in the model's own UV layout."""
        for target, value in sorted(coupled.items()):
            path = value['source']
            evidence = {'kind': 'converted_model_texture', 'rank': RANK_MODEL_TEXTURE}
            output = self._add_output(target, {'kind': 'direct', 'source': path}, [path], evidence,
                                      coupled=True, reference=value['reference'])
            if output is not None:
                output['reference'] = value['reference']
                self._mark_source(path, 'model_texture', None, target)

    def _plan_eyes(self, chosen_paths):
        """Merge each Java eye layer into the Bedrock texture of the body it is drawn over."""
        for eye_identity, body_identities in EYE_LAYERS.items():
            eye_path = chosen_paths.get(eye_identity)
            for body_identity in body_identities:
                body_path = chosen_paths.get(body_identity)
                if body_path is None:
                    continue
                for output in self.outputs.values():
                    if output['recipe']['kind'] != 'direct' or output['recipe'].get('source') != body_path:
                        continue
                    # A model texture has its own layout, so only an authored eye layer is merged into it.
                    if body_path in self.coupled_sources and eye_path is None:
                        continue
                    output['recipe'] = {'kind': 'eyes', 'source': body_path, 'eyes': eye_path}
                    if eye_path:
                        output['sources'] = sorted(set(output['sources']) | {eye_path})
                        self._mark_source(eye_path, 'merged', None, output['target'])
            if eye_path and not any(eye_path in output['sources'] for output in self.outputs.values()):
                self._mark_source(eye_path, 'unsupported', 'Bedrock draws eyes inside the body texture; '
                                  'the pack ships no body texture to carry them')

    def _add_output(self, target, recipe, sources, evidence, *, coupled=False, reference=None):
        """Plan one Bedrock texture unless a higher-ranked plan has it; returns the output or None.

        reference is the vanilla Bedrock texture to take the format and alpha codes from (target by default).
        """
        image, extension = self.vanilla_bedrock(reference or target)
        if image is None:
            return None
        codes = alpha_codes(image)
        alpha_mode = None
        if codes:
            if not coupled:
                alpha_mode = 'per_pixel'
            else:
                alpha_mode, codes = _uniform_alpha_code(image, codes)
        output = {'target': target, 'extension': extension, 'recipe': recipe, 'sources': sources,
                  'alpha_codes': alpha_mode, 'codes': codes, 'evidence': evidence, 'coupled': coupled,
                  'emissive_suffix': self.emissive_suffix}
        previous = self.outputs.get(target)
        if previous and previous['evidence'].get('rank', 0) > evidence.get('rank', 0):
            return None
        self.outputs[target] = output
        return output

    def _mark_source(self, path, status, reason, target=None):
        """Set the status, and the reason and target when given, of an authored texture's records."""
        for record in self.records:
            if record['source'] != path:
                continue
            record['status'] = status
            if reason:
                record['reason'] = reason
            if target:
                record.setdefault('targets', [])
                if target not in record['targets']:
                    record['targets'].append(target)


def _chosen_paths(identities):
    """Identity -> the authored path that supplies it; a current Java path wins over an older one."""
    chosen = {}
    for path, (identity, path_kind) in identities.items():
        if identity is None:
            continue
        current = chosen.get(identity)
        if current is None or (identities[current][1] != 'current' and path_kind == 'current'):
            chosen[identity] = path
    return chosen


def _uniform_alpha_code(image, codes):
    """('uniform', [code]) when one alpha code covers at least 90% of the opaque pixels, else (None, codes).

    A model texture has its own layout: only a code covering the whole body carries over.
    """
    alpha = _rgba_array(image)[..., 3]
    opaque = alpha > 0
    values, counts = np.unique(alpha[opaque], return_counts=True)
    most_common = int(values[np.argmax(counts)]) if len(values) else 255
    if most_common not in (0, 255) and counts.max() >= 0.9 * opaque.sum():
        return 'uniform', [most_common]
    return None, codes


# --- building ---

def build_output(output, read, vanilla_bedrock):
    """Return {'color', 'normal', 'mers', 'losses'} for one planned output, or None when it cannot be built.

    read(path) returns bytes or None; vanilla_bedrock is the vanilla target image.
    """
    recipe = output['recipe']
    if recipe['kind'] == 'remap':
        maps = _remapped_maps(recipe, read)
    else:
        maps = _direct_maps(recipe, read, output.get('emissive_suffix', '_e'))
    if maps is None:
        return None
    color, normal, specular = maps
    if normal is not None and normal.size != color.size:
        normal = _resized(normal, color.size)
    if specular is not None and specular.size != color.size:
        specular = _resized(specular, color.size)
    eye_mask = None
    if recipe['kind'] == 'eyes':
        eyes = decode(read(recipe['eyes'])) if recipe.get('eyes') else None
        color, eye_mask = merge_eyes(color, eyes, vanilla_bedrock, keep_vanilla_eyes=recipe.get('eyes') is None)
    elif output.get('alpha_codes') in ('per_pixel', 'uniform'):
        color = _with_vanilla_alpha_codes(color, vanilla_bedrock, output['alpha_codes'], output.get('codes', []))
    if eye_mask is not None and eye_mask.any():
        eye_specular = _companion_image(read, recipe['eyes'], '_s') if recipe.get('eyes') else None
        specular = _eyes_into_specular(specular, eye_mask, eye_specular, color.size)
    converted = decode_labpbr(normal, specular, policy=LABPBR_POLICY)
    return {'color': color, 'normal': converted['images'].get('normal'),
            'mers': converted['images'].get('metalness_emissive_roughness_subsurface'),
            'losses': converted['renderer_losses']}


def merge_eyes(body, eyes, vanilla_bedrock, *, keep_vanilla_eyes=False):
    """Bedrock eye pixels: the eye colour with a small alpha (alpha-as-emissive), as in vanilla Bedrock.

    Without an authored eye layer the eye positions come from the vanilla Bedrock
    alpha codes and keep the pack's own body colour there.
    """
    pixels = _rgba_array(body).copy()
    code = eye_alpha_code(vanilla_bedrock)
    if eyes is not None:
        overlay = _rgba_array(_resized(eyes, body.size))
        mask = overlay[..., 3] > 0
        pixels[..., :3] = np.where(mask[..., None], overlay[..., :3], pixels[..., :3])
    elif keep_vanilla_eyes and vanilla_bedrock is not None:
        reference = _rgba_array(_resized(vanilla_bedrock, body.size))
        mask = (reference[..., 3] > 0) & (reference[..., 3] < 255)
    else:
        mask = np.zeros(pixels.shape[:2], bool)
    alpha = np.where(pixels[..., 3] > 0, 255, 0)
    pixels[..., 3] = np.where(mask, code, alpha).astype(np.uint8)
    return Image.fromarray(pixels), mask


def eye_alpha_code(vanilla_bedrock):
    """The alpha code of the eye pixels of a vanilla Bedrock texture: its rarest code (eyes are small)."""
    codes = alpha_codes(vanilla_bedrock) if vanilla_bedrock is not None else []
    if not codes:
        return DEFAULT_EYE_ALPHA
    values, counts = np.unique(_rgba_array(vanilla_bedrock)[..., 3], return_counts=True)
    ranked = sorted((count, value) for value, count in zip(values.tolist(), counts.tolist()) if value not in (0, 255))
    return ranked[0][1] if ranked else DEFAULT_EYE_ALPHA


def _direct_maps(recipe, read, emissive_suffix):
    """(colour, normal, specular) of one authored texture with its emissive overlay drawn in; None without a source."""
    source = recipe.get('source')
    if not source:
        return None
    color = decode(read(source))
    normal = _companion_image(read, source, '_n')
    specular = _companion_image(read, source, '_s')
    emissive = _companion_image(read, source, emissive_suffix)
    if emissive is not None:
        emissive = _resized(emissive, color.size)
        color = Image.alpha_composite(color, emissive)
        specular = _emissive_into_specular(specular, emissive, color.size)
    return color, normal, specular


def _remapped_maps(recipe, read):
    """(colour, normal, specular) of a rectangle remap; None when a source role is missing."""
    sources = {role: decode(read(path)) for role, path in recipe['roles'].items() if path}
    if recipe['missing']:
        return None
    uv_sizes = {role: tuple(size) for role, size in recipe['uv'].items()}
    color = apply_rects(sources, uv_sizes, recipe['rects'], recipe['target_uv'])
    if recipe.get('role_alpha'):
        color = _with_role_alpha(color, recipe)
    # A PBR map is remapped only when every source has one.
    normals = {role: _companion_image(read, path, '_n') for role, path in recipe['roles'].items()}
    speculars = {role: _companion_image(read, path, '_s') for role, path in recipe['roles'].items()}
    normal = specular = None
    if all(image is not None for image in normals.values()):
        # Flat normals where no rectangle lands.
        normal = apply_rects(normals, uv_sizes, recipe['rects'], recipe['target_uv'], normal=True,
                             fill=(128, 128, 255, 255))
        normal = _resized(normal, color.size)
    if all(image is not None for image in speculars.values()):
        specular = apply_rects(speculars, uv_sizes, recipe['rects'], recipe['target_uv'], fill=(0, 0, 0, NO_EMISSION))
        specular = _resized(specular, color.size)
    return color, normal, specular


def _with_role_alpha(color, recipe):
    """Give each remapped rectangle the alpha its role needs on every opaque pixel (the sheep: body 3, wool 255)."""
    pixels = _rgba_array(color).copy()
    density = color.width / recipe['target_uv'][0]
    for role, _, (x, y, width, height), _ in recipe['rects']:
        rows = slice(round(y * density), round((y + height) * density))
        columns = slice(round(x * density), round((x + width) * density))
        region = pixels[rows, columns]
        region[..., 3] = np.where(region[..., 3] > 0, recipe['role_alpha'][role], 0)
    return Image.fromarray(pixels)


def _companion_image(read, path, suffix):
    """The image beside path with a suffix (LabPBR '_n'/'_s' map, emissive overlay), or None."""
    data = read(path[:-4] + suffix + '.png')
    return decode(data) if data is not None else None


def _with_vanilla_alpha_codes(color, vanilla, mode, codes):
    """Put the vanilla Bedrock alpha codes under the author's colours; transparent pixels stay transparent.

    uniform: one code on every opaque pixel. per_pixel: the vanilla alpha where
    vanilla is opaque, fully opaque elsewhere.
    """
    pixels = _rgba_array(color).copy()
    opaque = pixels[..., 3] > 0
    if mode == 'uniform' and len(codes) == 1:
        pixels[..., 3] = np.where(opaque, codes[0], 0)
    elif mode == 'per_pixel':
        reference = _rgba_array(_resized(vanilla, color.size))[..., 3]
        pixels[..., 3] = np.where(opaque, np.where(reference > 0, reference, 255), 0)
    return Image.fromarray(pixels)


def _specular_base(specular, size):
    """The specular map at size, or one with no emission anywhere when the texture has none."""
    if specular is not None:
        return _rgba_array(_resized(specular, size)).copy()
    base = np.zeros((size[1], size[0], 4), np.uint8)
    base[..., 3] = NO_EMISSION
    return base


def _emissive_into_specular(specular, emissive, size):
    """The emissive overlay's pixels glow at full emission."""
    base = _specular_base(specular, size)
    glowing = _rgba_array(emissive)[..., 3] > 0
    base[..., 3] = np.where(glowing, FULL_EMISSION, base[..., 3])
    return Image.fromarray(base)


def _eyes_into_specular(specular, mask, eye_specular, size):
    """Eyes draw at full brightness in Java: full LabPBR emission, unless the eye layer's specular map sets one."""
    base = _specular_base(specular, size)
    emission = np.full(mask.shape, FULL_EMISSION, np.uint8)
    if eye_specular is not None:
        authored = _rgba_array(_resized(eye_specular, size))
        emission = np.where(authored[..., 3] < 255, authored[..., 3], FULL_EMISSION).astype(np.uint8)
        base[..., :3] = np.where(mask[..., None], authored[..., :3], base[..., :3])
    base[..., 3] = np.where(mask, emission, base[..., 3])
    return Image.fromarray(base)
