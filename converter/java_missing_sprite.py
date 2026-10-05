"""Java's built-in missing-texture sprite, for face slots that name no texture.

Java draws such a face with its magenta and black checkerboard; the converted pack
shows the same, so a gap in the author's model looks the same in both games.
"""
import io

from PIL import Image

MISSING_MODEL_SPRITE = 'assets/bct_builtin/textures/block/java_missingno.png'
MAGENTA = (248, 0, 248, 255)
BLACK = (0, 0, 0, 255)


def missing_model_png():
    """PNG bytes of the 16x16 checkerboard: magenta top right and bottom left, black elsewhere."""
    image = Image.new('RGBA', (16, 16))
    image.putdata([MAGENTA if (x < 8) != (y < 8) else BLACK for y in range(16) for x in range(16)])
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()
