"""Heights of terrain transition surfaces above their host block's top face, in block units.

Legacy entity surfaces and single-material native blocks all sit at one small clearance.
Shared native blocks count their height in pixels of a 256 texture instead, one pixel per
priority tier unless an effect sets its own height_pixels.
"""

# Clearance of legacy entity and single-material native surfaces.
SURFACE_BASE = 5 / 1024
# Shared native surfaces measure height in pixels of a texture this wide.
TEXTURE_RESOLUTION = 256
PIXELS_PER_TIER = 1
PRIORITY_STEP = PIXELS_PER_TIER / TEXTURE_RESOLUTION


def surface_height(layer, face=0):
    """Clearance of a legacy entity or single-material surface; every layer and face shares it."""
    return SURFACE_BASE


def native_surface_height(material, layer=2, priority=1):
    """Default height of a shared native surface: one step per priority tier."""
    return priority * PRIORITY_STEP
