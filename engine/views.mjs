/**
 * Where players look and travel. Replacement blocks (replacement.mjs) and leaf
 * model blocks (leaves.mjs) change only where no player sees them change, and
 * every part works on the chunks players head for first; the engine keeps one
 * views object for all of them.
 *
 * With farDistance 0 (the default) nothing waits: blocks change as soon as they
 * load or are placed. A change at a block would be seen when the block lies within farDistance of
 * a player's head and within half of viewAngle of the player's view direction
 * (getHeadLocation, getViewDirection). The cone is widened by the block's own
 * size, so a block counts while any part of it is in view. For joinTicks after
 * a player joins, changes dimension or teleports, nearDistance takes the place
 * of farDistance for that player, so the area around a new arrival converts
 * while it loads. `stamp` changes when a head moves or a view turns enough to
 * look at waiting changes again, and when players come or go. The settings are
 * the `replace` ones (settings.mjs).
 *
 * A player's heading is the way they travel (their movement over the last
 * ticks), or the way they look while they stand still. `rank` orders chunks
 * nearest first, ahead before behind at the same distance.
 */
const SIDES = [[1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0], [0, 0, 1], [0, 0, -1]];
// Half a block's diagonal: a block shows while any part of it is in view, not only its center.
const BLOCK_RADIUS = 0.87;
// Eye height above the feet, for a player whose head location the game cannot give yet.
const EYE_HEIGHT = 1.62;
// A player who moves farther than this between two ticks has arrived somewhere new (a teleport,
// not an ender pearl): about the default swap radius, beyond which nothing was converted yet.
const ARRIVAL_JUMP = 64;
// How far a head moves (blocks) or a view turns (cosine of 5 degrees) before waiting changes are looked at again.
const VIEW_MOVE = 0.5, VIEW_TURN = Math.cos(Math.PI / 36);
// Blocks per tick a player must move to travel somewhere (slower, the view direction is the heading).
const TRAVEL_SPEED = 0.15;
// How much a heading moves a chunk's rank: a fifth of its distance nearer ahead, farther behind.
const HEADING_PULL = 0.2;
// Half a chunk column's horizontal diagonal (blocks): a chunk is in view while any part of it is.
const CHUNK_RADIUS = 11.4;

/** limits(): { viewAngle, farDistance, nearDistance, joinTicks }. */
export function createViews({ system, limits }) {
  const arrivals = new Map();   // player id -> { dimension, tick, x, y, z, seen, mx, mz } where the player was and when they arrived; motion
  const marks = new Map();      // player id -> the view `stamp` last changed for
  let views = [], halfAngle = Math.PI, stamp = 0, readTick, readPlayers;

  /** Reads every player's head, view direction and heading, once per tick however many parts ask. */
  function update(players) {
    if (readTick === system.currentTick && readPlayers === players) return;
    readTick = system.currentTick; readPlayers = players;
    const { viewAngle, farDistance, nearDistance, joinTicks } = limits();
    const tick = system.currentTick, ids = new Set();
    halfAngle = viewAngle * Math.PI / 360;
    views = [];
    let moved = false;
    for (const player of players) {
      let dimension, feet;
      try { dimension = player.dimension.id; feet = player.location; } catch { continue; }
      ids.add(player.id);
      let arrival = arrivals.get(player.id);
      if (!arrival || arrival.dimension !== dimension ||
        Math.max(Math.abs(feet.x - arrival.x), Math.abs(feet.y - arrival.y), Math.abs(feet.z - arrival.z)) > ARRIVAL_JUMP) {
        arrival = { dimension, tick, seen: tick, x: feet.x, y: feet.y, z: feet.z, mx: 0, mz: 0 };
        arrivals.set(player.id, arrival);
      }
      // Movement per tick, smoothed over the last few reads.
      const ticks = Math.max(1, tick - arrival.seen);
      arrival.mx = arrival.mx * 0.6 + (feet.x - arrival.x) / ticks * 0.4;
      arrival.mz = arrival.mz * 0.6 + (feet.z - arrival.z) / ticks * 0.4;
      arrival.x = feet.x; arrival.y = feet.y; arrival.z = feet.z; arrival.seen = tick;
      let head, direction;
      try { head = player.getHeadLocation(); } catch { /* not spawned yet */ }
      try { direction = player.getViewDirection(); } catch { /* not spawned yet */ }
      head ??= { x: feet.x, y: feet.y + EYE_HEIGHT, z: feet.z };
      const length = direction ? Math.hypot(direction.x, direction.y, direction.z) : 0;
      const view = { dimension, x: head.x, y: head.y, z: head.z,
        limit: tick - arrival.tick < joinTicks ? Math.min(nearDistance, farDistance) : farDistance,
        // Without a view direction the player could be looking anywhere.
        direction: length > 0 ? { x: direction.x / length, y: direction.y / length, z: direction.z / length } : undefined };
      const speed = Math.hypot(arrival.mx, arrival.mz), flat = view.direction && Math.hypot(view.direction.x, view.direction.z);
      view.heading = speed > TRAVEL_SPEED ? { x: arrival.mx / speed, z: arrival.mz / speed }
        : flat > 0.3 ? { x: view.direction.x / flat, z: view.direction.z / flat } : undefined;
      views.push(view);
      const mark = marks.get(player.id);
      if (!mark || mark.dimension !== dimension || mark.limit !== view.limit || !mark.direction !== !view.direction ||
        Math.hypot(view.x - mark.x, view.y - mark.y, view.z - mark.z) > VIEW_MOVE ||
        (view.direction && view.direction.x * mark.direction.x + view.direction.y * mark.direction.y + view.direction.z * mark.direction.z < VIEW_TURN)) {
        marks.set(player.id, view);
        moved = true;
      }
    }
    for (const id of [...arrivals.keys()]) if (!ids.has(id)) arrivals.delete(id);
    for (const id of [...marks.keys()]) if (!ids.has(id)) { marks.delete(id); moved = true; }
    if (moved) stamp++;
  }

  /** Whether some player could see a change at a block: the block within the player's distance and its outline within the view cone. */
  function sees(dimensionId, location) {
    for (const view of views) {
      if (view.dimension !== dimensionId || view.limit <= 0) continue;
      const x = location.x + 0.5 - view.x, y = location.y + 0.5 - view.y, z = location.z + 0.5 - view.z;
      const squared = x * x + y * y + z * z;
      if (squared > view.limit * view.limit) continue;
      if (!view.direction) return true;
      const distance = Math.sqrt(squared);
      if (distance <= BLOCK_RADIUS) return true;
      const cosine = (x * view.direction.x + y * view.direction.y + z * view.direction.z) / distance;
      if (Math.acos(Math.max(-1, Math.min(1, cosine))) - Math.asin(BLOCK_RADIUS / distance) <= halfAngle) return true;
    }
    return false;
  }

  /** Whether some player could see a change anywhere in a box of blocks (min to max, inclusive); may say yes when no block is seen. */
  function seesBox(dimensionId, min, max) {
    const center = { x: (min.x + max.x + 1) / 2, y: (min.y + max.y + 1) / 2, z: (min.z + max.z + 1) / 2 };
    const radius = Math.hypot(max.x - min.x + 1, max.y - min.y + 1, max.z - min.z + 1) / 2;
    for (const view of views) {
      if (view.dimension !== dimensionId || view.limit <= 0) continue;
      // The nearest point of the box: beyond the player's distance nothing in it is seen.
      const nx = Math.max(min.x, Math.min(view.x, max.x + 1)), ny = Math.max(min.y, Math.min(view.y, max.y + 1)), nz = Math.max(min.z, Math.min(view.z, max.z + 1));
      if (Math.hypot(nx - view.x, ny - view.y, nz - view.z) > view.limit) continue;
      if (!view.direction) return true;
      const x = center.x - view.x, y = center.y - view.y, z = center.z - view.z, distance = Math.hypot(x, y, z);
      if (distance <= radius) return true;
      const cosine = (x * view.direction.x + y * view.direction.y + z * view.direction.z) / distance;
      if (Math.acos(Math.max(-1, Math.min(1, cosine))) - Math.asin(radius / distance) <= halfAngle) return true;
    }
    return false;
  }

  /** Whether some player could see every block of a box (min to max, inclusive): all of it within their distance and cone. */
  function seesAll(dimensionId, min, max) {
    const center = { x: (min.x + max.x + 1) / 2, y: (min.y + max.y + 1) / 2, z: (min.z + max.z + 1) / 2 };
    const radius = Math.hypot(max.x - min.x + 1, max.y - min.y + 1, max.z - min.z + 1) / 2;
    for (const view of views) {
      if (view.dimension !== dimensionId || view.limit <= 0) continue;
      // The farthest corner of the box within the player's distance.
      const fx = Math.max(Math.abs(min.x - view.x), Math.abs(max.x + 1 - view.x)), fy = Math.max(Math.abs(min.y - view.y), Math.abs(max.y + 1 - view.y));
      const fz = Math.max(Math.abs(min.z - view.z), Math.abs(max.z + 1 - view.z));
      if (fx * fx + fy * fy + fz * fz > view.limit * view.limit) continue;
      if (!view.direction) return true;
      const x = center.x - view.x, y = center.y - view.y, z = center.z - view.z, distance = Math.hypot(x, y, z);
      if (distance <= radius) continue;
      const cosine = (x * view.direction.x + y * view.direction.y + z * view.direction.z) / distance;
      if (Math.acos(Math.max(-1, Math.min(1, cosine))) + Math.asin(radius / distance) <= halfAngle) return true;
    }
    return false;
  }

  /**
   * How soon a chunk matters (lower is sooner): its distance in blocks from the
   * nearest player, so the blocks around a player always come first. Ahead of a
   * player's heading a chunk counts up to a fifth nearer and behind up to a
   * fifth farther, which only breaks ties between rings. With `waits` (changes that wait while seen) a chunk
   * a player could see within their distance comes after the band just beyond
   * it: its blocks would mostly wait, the band beyond comes into view next.
   */
  function rank(dimensionId, cx, cz, waits = false) {
    let best = Infinity;
    for (const view of views) {
      if (view.dimension !== dimensionId) continue;
      const dx = cx * 16 + 8 - view.x, dz = cz * 16 + 8 - view.z, distance = Math.hypot(dx, dz);
      let score = distance;
      if (view.heading && distance > CHUNK_RADIUS) score = distance * (1 - HEADING_PULL * (dx * view.heading.x + dz * view.heading.z) / distance);
      if (waits && distance < view.limit + CHUNK_RADIUS) {
        const flat = view.direction ? Math.hypot(view.direction.x, view.direction.z) : 0;
        const along = flat > 0.1 && distance > 0 ? (dx * view.direction.x + dz * view.direction.z) / (distance * flat) : 1;
        if (distance <= CHUNK_RADIUS || Math.acos(Math.max(-1, Math.min(1, along))) - Math.asin(Math.min(1, CHUNK_RADIUS / distance)) <= halfAngle)
          score += 2 * view.limit;
      }
      if (score < best) best = score;
    }
    return best;
  }

  return { update, sees, seesBox, seesAll, rank, get stamp() { return stamp; }, get list() { return views; } };
}

/** Views for a part used on its own: no player is ever looking, and chunks go nearest first by their own measure. */
export const NO_VIEWS = Object.freeze({ update() {}, sees: () => false, seesBox: () => false, seesAll: () => false, rank: () => 0, stamp: 0, list: [] });

/** Whether a block shows: some neighbor is not an opaque full cube (solid(type)). Unloaded neighbors count as covering it. */
export function showsAt(dimension, location, solid) {
  for (const [dx, dy, dz] of SIDES) {
    let type;
    try { type = dimension.getBlock({ x: location.x + dx, y: location.y + dy, z: location.z + dz })?.typeId; } catch { type = undefined; }
    if (type !== undefined && !solid(type)) return true;
  }
  return false;
}
