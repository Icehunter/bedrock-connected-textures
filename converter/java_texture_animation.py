"""Turn Java sprite animations (.png.mcmeta) into Bedrock flipbooks.

Bedrock plays a flipbook as a fixed list of frames, one every ticks_per_frame ticks, so a
Java animation's frame times, per-channel timelines and interpolation are baked into such a
list. Identical frames are stored once. Interpolated frames are blended at whole ticks;
nothing is downscaled to fit, an animation that is too large fails instead.
"""
from dataclasses import dataclass
from functools import reduce
import hashlib
import math

import numpy as np
from PIL import Image

ANIMATION_FIELDS = {'width', 'height', 'frametime', 'frames', 'interpolate'}
# Image modes that are written as they are; other modes are kept as the RGBA they were built in.
WRITTEN_MODES = ('L', 'RGB', 'RGBA')


@dataclass(frozen=True)
class Animation:
    width: int
    height: int
    frames: tuple  # (frame index, ticks) pairs in play order
    interpolate: bool

    @property
    def ticks(self):
        return sum(duration for _, duration in self.frames)


def parse_animation(size, metadata, *, invalid_frames='error', diagnostics=None):
    """Parse animation timing, optionally matching Java 26.2's frame filtering.

    Java's metadata codec rejects negative indices and durations below one. SpriteContents
    then warns and drops indices beyond the image's frame count, and zero or one surviving
    entries give a still sprite showing frame zero. invalid_frames='java' does the same,
    noting each change in diagnostics; the default 'error' stays strict for checking newly
    written metadata.
    """
    if invalid_frames not in ('error', 'java'):
        raise ValueError('Unknown invalid-frame policy')
    if diagnostics is not None and not isinstance(diagnostics, list):
        raise TypeError('Animation diagnostics must be a list')
    diagnostics = diagnostics if diagnostics is not None else []
    data = metadata.get('animation')
    if not isinstance(data, dict):
        raise ValueError('Missing Java animation section')
    unknown = set(data) - ANIMATION_FIELDS
    if unknown:
        if invalid_frames == 'error':
            raise ValueError('Unknown Java animation fields: ' + str(sorted(unknown)))
        diagnostics.append({'code': 'ignored_animation_fields', 'fields': sorted(unknown)})
    frame_width, frame_height = _frame_size(size, data)
    width, height = size
    frame_count = (width // frame_width) * (height // frame_height)
    duration = data.get('frametime', 1)
    if type(duration) is not int or duration <= 0:
        raise ValueError('Invalid animation frametime')
    entries = data.get('frames', list(range(frame_count)))
    if not isinstance(entries, list):
        raise ValueError('Animation frames must be a list')
    if not entries and invalid_frames == 'error':
        entries = list(range(frame_count))
    frames = []
    for position, entry in enumerate(entries):
        index, time = _frame_entry(entry, duration, invalid_frames)
        if index >= frame_count:
            if invalid_frames == 'error':
                raise ValueError('Animation frame index/time outside range')
            diagnostics.append({'code': 'invalid_frame_index', 'frame_position': position, 'index': index,
                                'frame_count': frame_count, 'action': 'removed'})
            continue
        frames.append((index, time))
    interpolate = data.get('interpolate', False)
    if type(interpolate) is not bool:
        raise ValueError('Invalid animation interpolation flag')
    if invalid_frames == 'java':
        if 'frames' in data:
            unused = sorted(set(range(frame_count)) - {index for index, _ in frames})
            if unused:
                diagnostics.append({'code': 'unused_frames', 'indices': unused})
        if len(frames) <= 1:
            diagnostics.append({'code': 'java_static_sprite', 'surviving_entries': len(frames), 'effective_frame': 0})
            return Animation(frame_width, frame_height, ((0, 1),), False)
    return Animation(frame_width, frame_height, tuple(frames), interpolate)


def sprite_frames(image, animation):
    """Every frame of a sprite sheet as RGBA images, left to right, then top to bottom."""
    width, height = animation.width, animation.height
    columns = image.width // width
    frame_count = columns * (image.height // height)
    frames = []
    for index in range(frame_count):
        left, top = (index % columns) * width, (index // columns) * height
        frames.append(image.crop((left, top, left + width, top + height)).convert('RGBA'))
    return frames


def compile_flipbook(image, animation, texture, atlas_tile, max_ticks=65536):
    """compile_family for a colour image alone: (frame strip, flipbook entry).

    Java interpolation keeps the current frame's alpha. Blending between whole ticks is not
    reproduced; frames are baked at integer ticks only.
    """
    images, entry = compile_family({'color': image}, animation, texture, atlas_tile, max_ticks=max_ticks)
    return images['color'], entry


def compile_family(images, animation, texture, atlas_tile, max_ticks=65536, max_side=16384,
                   channel_animations=None, color_channels=None, layout='strip'):
    """Bake one frame table shared by the colour image and every Bedrock material channel.

    A channel may have its own timeline (channel_animations); the table then runs for the
    least common multiple of all timelines so every channel shows the right frame at every
    tick. Channels in color_channels keep the current frame's alpha when blending, as Java
    does. layout 'strip' stacks the frames vertically; 'grid' fills rows of a square-ish grid.
    Returns ({channel: image}, flipbook entry).
    """
    if 'color' not in images:
        raise ValueError('Animated material requires color')
    if layout not in ('strip', 'grid'):
        raise ValueError('Unknown animation texture layout')
    if animation.width != animation.height:
        raise ValueError('Native flipbook export requires square frames')
    channel_animations = channel_animations or {}
    color_channels = {'color'} if color_channels is None else set(color_channels)
    if not color_channels <= set(images):
        raise ValueError('Color animation channel is absent')
    if set(channel_animations) - set(images):
        raise ValueError('Animation metadata references an absent material channel')
    frames, timelines = {}, {}
    for channel, image in images.items():
        timeline = _channel_timeline(image, images['color'].size, animation, channel_animations.get(channel))
        frames[channel] = sprite_frames(image, timeline)
        timelines[channel] = timeline
    cycle = math.lcm(*(timeline.ticks for timeline in timelines.values()))
    if cycle > max_ticks:
        raise ValueError('Animation exceeds the configured timeline budget')
    # Blended frames change every tick. Otherwise every frame change falls on a multiple of
    # the greatest common divisor of all frame times, so one table entry per gcd ticks is enough.
    if any(timeline.interpolate for timeline in timelines.values()):
        tick_step = 1
    else:
        tick_step = reduce(math.gcd, (duration for timeline in timelines.values() for _, duration in timeline.frames))
    if layout == 'grid':
        capacity = (max_side // animation.width) * (max_side // animation.height)
    else:
        capacity = max_side // animation.height
    unique_frames = {channel: [] for channel in images}
    frame_of_digest = {}
    indices = []
    for tick in range(0, cycle, tick_step):
        shown = {channel: _frame_at(timelines[channel], channel_frames, tick, channel in color_channels)
                 for channel, channel_frames in frames.items()}
        digest = hashlib.sha256(b''.join(shown[channel].tobytes() for channel in sorted(shown))).digest()
        if digest not in frame_of_digest:
            if animation.width > max_side or len(unique_frames['color']) + 1 > capacity:
                raise ValueError('Animated strip exceeds the configured texture-size budget; no downsampling performed')
            frame_of_digest[digest] = len(unique_frames['color'])
            for channel, image in shown.items():
                unique_frames[channel].append(image)
        indices.append(frame_of_digest[digest])
    frame_total = len(unique_frames['color'])
    if layout == 'strip':
        columns = 1
    else:
        columns = min(max_side // animation.width, math.ceil(math.sqrt(frame_total)))
    rows = math.ceil(frame_total / columns)
    if rows * animation.height > max_side:
        raise ValueError('Animated grid exceeds texture-size budget')
    sheets = {}
    for channel, channel_frames in unique_frames.items():
        sheet = _frame_sheet(channel_frames, columns, rows, animation.width, animation.height)
        mode = images[channel].mode
        sheets[channel] = sheet.convert(mode) if mode in WRITTEN_MODES else sheet
    entry = {'flipbook_texture': texture, 'atlas_tile': atlas_tile, 'ticks_per_frame': tick_step,
             'frames': indices, 'blend_frames': False}
    if layout == 'grid':
        entry.update(layout='grid', columns=columns, rows=rows,
                     frame_width=animation.width, frame_height=animation.height)
    return sheets, entry


def uv_animation_xy(flipbook):
    """Entity UV offset and scale expressions that step through a strip or grid with the level clock.

    Carriers are entities, which have no flipbooks; their render controller moves the UV
    window over the frames instead, in step with the block flipbooks.
    """
    columns = flipbook.get('columns', 1)
    rows = flipbook.get('rows', max(flipbook['frames']) + 1)
    padding = flipbook.get('padding', 0)
    cell_width = flipbook.get('cell_width', 1)
    cell_height = flipbook.get('cell_height', 1)
    pixel_width = flipbook.get('pixel_width', columns)
    pixel_height = flipbook.get('pixel_height', rows)
    phase = f"math.mod(math.floor(q.time_stamp / {flipbook['ticks_per_frame']}), {len(flipbook['frames'])})"
    x_offsets = [((index % columns) * cell_width + padding) / pixel_width for index in flipbook['frames']]
    y_offsets = [((index // columns) * cell_height + padding) / pixel_height for index in flipbook['frames']]
    return {'offset': [_stepped_expression(phase, x_offsets), _stepped_expression(phase, y_offsets)],
            'scale': [(cell_width - 2 * padding) / pixel_width, (cell_height - 2 * padding) / pixel_height]}


def _frame_size(size, data):
    """(frame width, frame height) from the metadata; frames must divide the image exactly."""
    width, height = size
    if 'width' in data:
        frame_width, frame_height = data['width'], data.get('height', height)
    elif 'height' in data:
        frame_width, frame_height = width, data['height']
    else:
        # Java's default: square frames as wide as the narrower side.
        frame_width = frame_height = min(size)
    if (any(type(value) is not int or value <= 0 for value in (frame_width, frame_height))
            or width % frame_width or height % frame_height):
        raise ValueError('Animation dimensions do not divide the sprite')
    return frame_width, frame_height


def _frame_entry(entry, duration, invalid_frames):
    """(frame index, ticks) of one "frames" entry: an index, or {"index", "time"}."""
    if type(entry) is int:
        index, time = entry, duration
    elif (isinstance(entry, dict) and (invalid_frames == 'java' or set(entry) <= {'index', 'time'})
          and 'index' in entry):
        index, time = entry['index'], entry.get('time', duration)
    else:
        raise ValueError('Invalid animation frame')
    if type(index) is not int or index < 0 or type(time) is not int or time <= 0:
        raise ValueError('Animation frame index/time outside range')
    return index, time


def _channel_timeline(image, color_size, animation, own_animation):
    """The timeline a channel follows: its own, the colour's, or one still frame held throughout."""
    if own_animation is not None:
        if (own_animation.width, own_animation.height) != (animation.width, animation.height):
            raise ValueError('Material frame dimensions differ')
        return own_animation
    if image.size == color_size:
        return animation
    if image.size == (animation.width, animation.height):
        return Animation(animation.width, animation.height, ((0, animation.ticks),), False)
    raise ValueError('Animated material channel dimensions do not match')


def _frame_at(timeline, frames, tick, keep_alpha):
    """The image a channel shows at a tick: its current frame, blended toward the next one when interpolating.

    keep_alpha keeps the current frame's alpha as Java does for colour; it also keeps
    channels whose alpha is data (the emission sentinel, normal-map height) unblended.
    """
    elapsed = tick % timeline.ticks
    for step, (index, duration) in enumerate(timeline.frames):
        if elapsed < duration:
            break
        elapsed -= duration
    current = frames[index]
    if not (timeline.interpolate and elapsed):
        return current
    following = timeline.frames[(step + 1) % len(timeline.frames)][0]
    first = np.asarray(current, dtype=np.uint8)
    second = np.asarray(frames[following], dtype=np.uint8)
    rgb = _blend(first[:, :, :3], second[:, :, :3], elapsed, duration)
    alpha = first[:, :, 3] if keep_alpha else _blend(first[:, :, 3], second[:, :, 3], elapsed, duration)
    return Image.fromarray(np.dstack((rgb, alpha)))


def _blend(first, second, elapsed, duration):
    """Pixels part way from first to second, by elapsed of duration ticks, rounded down."""
    return ((first.astype(np.uint32) * (duration - elapsed) + second.astype(np.uint32) * elapsed)
            // duration).astype(np.uint8)


def _frame_sheet(frames, columns, rows, width, height):
    """Frames laid out in rows of columns; spare cells repeat the last frame."""
    sheet = Image.new('RGBA', (width * columns, height * rows))
    for index, frame in enumerate(frames):
        sheet.paste(frame, ((index % columns) * width, (index // columns) * height))
    for index in range(len(frames), columns * rows):
        sheet.paste(frames[-1], ((index % columns) * width, (index // columns) * height))
    return sheet


def _stepped_expression(phase, values):
    """A Molang expression that gives values[phase], merging runs of equal values into one test."""
    runs = []
    for index, value in enumerate(values):
        if runs and runs[-1][1] == value:
            runs[-1] = (index + 1, value)
        else:
            runs.append((index + 1, value))
    expression = str(runs[-1][1])
    for end, value in reversed(runs[:-1]):
        expression = f'({phase} < {end} ? {value} : {expression})'
    return expression
