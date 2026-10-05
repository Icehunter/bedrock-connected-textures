"""Priority-exclusive quarter masks for the five surface tiers, without baking biome colours.

Each tier (priority 1 to 5) draws its edge cutout where it touches a host. Where the cutouts of
different tiers overlap, the higher tier wins, so a tier's coverage in a quarter of the face
depends on which tiers touch that quarter's edge, side and corner. catalog() cuts every distinct
mask once, for the north-east quarter; the other quarters reuse them turned a quarter turn at a
time.
"""
import itertools

import numpy as np

from edge_shapes import edge_alpha

# The engine accepts mask lookups 0-19 (engine/terrain-rules.mjs).
MAX_SHAPES = 20
# Pixel rows and columns of each quarter of a 256 square, clockwise from north-east.
QUARTERS = [(slice(0, 128), slice(128, 256)), (slice(128, 256), slice(128, 256)),
            (slice(128, 256), slice(0, 128)), (slice(0, 128), slice(0, 128))]
_TIERS = range(1, 6)
# A contact holds the tier touching it, or 0 when no surface does.
_CONTACT_VALUES = 6


def catalog(root, effects):
    """Cut each tier's quarter masks; returns {entity: masks} and sets every effect's mask_lookup.

    mask_lookup has an entry per combination of tiers on the quarter's edge, side and corner
    contacts, at edge + 6 * side + 36 * corner, holding an index into that tier's masks.
    Mask 0 is empty.
    """
    by_tier = {effect['priority']: effect for effect in effects}
    if set(by_tier) != set(_TIERS):
        raise ValueError('Quarter masking requires the five ordered surface tiers')
    contact_shapes = {tier: _contact_shapes(root, effect) for tier, effect in by_tier.items()}
    result = {}
    for tier, effect in by_tier.items():
        masks, lookup = _exclusive_masks(tier, contact_shapes)
        if len(masks) > MAX_SHAPES:
            message = f"{effect['entity']}: {len(masks)} edge shapes, the engine draws at most {MAX_SHAPES}"
            raise ValueError(message)
        effect['mask_lookup'] = lookup
        result[effect['entity']] = masks
    return result


def _contact_shapes(root, effect):
    """A tier's north-east quarter coverage from its edge, its side and the corner they share."""
    edge = np.asarray(edge_alpha(root, effect)) >= 128
    side = np.rot90(edge, -1)
    return [mask[QUARTERS[0]] for mask in (edge, side, edge & side)]


def _exclusive_masks(tier, contact_shapes):
    """One tier's distinct masks over every contact combination, cut away where higher tiers draw.

    Returns the masks, starting with the empty one, and each combination's mask index.
    """
    empty = np.zeros((128, 128), dtype=bool)
    masks = [empty]
    indices = {np.packbits(empty).tobytes(): 0}
    lookup = [0] * _CONTACT_VALUES ** 3
    for contacts in itertools.product(range(_CONTACT_VALUES), repeat=3):
        own = empty.copy()
        above = empty.copy()
        for position, source in enumerate(contacts):
            if source == tier:
                own |= contact_shapes[source][position]
            elif source > tier:
                above |= contact_shapes[source][position]
        mask = own & ~above
        key = np.packbits(mask).tobytes()
        if key not in indices:
            indices[key] = len(masks)
            masks.append(mask)
        edge, side, corner = contacts
        lookup[edge + _CONTACT_VALUES * side + _CONTACT_VALUES ** 2 * corner] = indices[key]
    return masks, lookup
