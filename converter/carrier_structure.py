"""Bedrock structure file that spawns one passive carrier entity.

The engine places this structure with entities only (no blocks) to spawn a
carrier above a block. Passive carriers run as area effect clouds on the server
(their server entity sets that runtime identifier), so the saved entity holds
the cloud's fields: no mob effects, no particles, a radius that never changes
and a duration no play session reaches.

Structure files are little-endian NBT. The encoder below covers only the tag
types this template uses.
"""
import struct


# NBT tag ids.
TAG_BYTE = 1
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10

_NUMBER_FORMATS = {TAG_BYTE: '<b', TAG_INT: '<i', TAG_LONG: '<q', TAG_FLOAT: '<f'}
# A cloud disappears after Duration ticks; this is close to the 32-bit limit
# (about three years of play).
_CLOUD_DURATION_TICKS = 2140000000


def cloud_structure(identifier):
    """Return .mcstructure bytes holding one carrier entity and no blocks."""
    structure = {
        'format_version': (TAG_INT, 1),
        'size': (TAG_LIST, (TAG_INT, [1, 1, 1])),
        'structure_world_origin': (TAG_LIST, (TAG_INT, [0, 0, 0])),
        'structure': (TAG_COMPOUND, {
            # -1 in both block layers is structure void: placing never changes blocks.
            'block_indices': (TAG_LIST, (TAG_LIST, [(TAG_INT, [-1]), (TAG_INT, [-1])])),
            'entities': (TAG_LIST, (TAG_COMPOUND, [_carrier_entity(identifier)])),
            'palette': (TAG_COMPOUND, {
                'default': (TAG_COMPOUND, {
                    'block_palette': (TAG_LIST, (TAG_COMPOUND, [])),
                    'block_position_data': (TAG_COMPOUND, {}),
                }),
            }),
        }),
    }
    # The root is an unnamed compound.
    return bytes([TAG_COMPOUND]) + _encode(TAG_STRING, '') + _encode(TAG_COMPOUND, structure)


def _carrier_entity(identifier):
    """Saved entity fields, centred on the block's top face."""
    return {
        'identifier': (TAG_STRING, identifier),
        'Pos': (TAG_LIST, (TAG_FLOAT, [.5, 0.0, .5])),
        'Rotation': (TAG_LIST, (TAG_FLOAT, [0.0, 0.0])),
        'Motion': (TAG_LIST, (TAG_FLOAT, [0.0, 0.0, 0.0])),
        'Persistent': (TAG_BYTE, 1),
        'Duration': (TAG_INT, _CLOUD_DURATION_TICKS),
        'DurationOnUse': (TAG_INT, 0),
        'Radius': (TAG_FLOAT, .5),
        'InitialRadius': (TAG_FLOAT, .5),
        'RadiusPerTick': (TAG_FLOAT, 0.0),
        'RadiusOnUse': (TAG_FLOAT, 0.0),
        'RadiusChangeOnPickup': (TAG_FLOAT, 0.0),
        'ParticleId': (TAG_INT, 0),
        'ParticleColor': (TAG_INT, 0),
        'mobEffects': (TAG_LIST, (TAG_COMPOUND, [])),
        'ReapplicationDelay': (TAG_INT, 20),
        'SpawnTick': (TAG_LONG, 0),
        'PickupCount': (TAG_INT, 0),
    }


def _encode(tag, value):
    """Encode one payload, without the type byte and name that precede it in a compound."""
    if tag in _NUMBER_FORMATS:
        return struct.pack(_NUMBER_FORMATS[tag], value)
    if tag == TAG_STRING:
        return _encode_string(value)
    if tag == TAG_LIST:
        item_tag, items = value
        return _encode_list(item_tag, items)
    if tag == TAG_COMPOUND:
        return _encode_compound(value)
    raise ValueError('Unsupported NBT tag')


def _encode_string(text):
    data = text.encode('utf-8')
    return struct.pack('<H', len(data)) + data


def _encode_list(item_tag, items):
    """A list stores its item type and count once, then the bare payloads."""
    payloads = b''.join(_encode(item_tag, item) for item in items)
    return bytes([item_tag]) + struct.pack('<i', len(items)) + payloads


def _encode_compound(fields):
    """Each field is its type byte, name and payload; a zero byte ends the compound."""
    encoded_fields = b''.join(bytes([tag]) + _encode_string(name) + _encode(tag, value)
                              for name, (tag, value) in fields.items())
    return encoded_fields + b'\0'
