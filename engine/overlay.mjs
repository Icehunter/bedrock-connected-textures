/**
 * Overlay tile selection for the 17-tile OptiFine overlay template, as the
 * Java renderers pick it.
 *
 * Template tiles, by where the overlay lies on the face (texture space):
 *    0 corner down+right   1 down        2 corner left+down
 *    3 down+right          4 left+down   5 left+down+right   6 left+down+up
 *    7 right               8 all four    9 left             10 right+up
 *   11 left+up            12 down+right+up                  13 left+right+up
 *   14 corner right+up    15 up         16 corner up+left
 * An overlay tile lies on the side of the neighbor that gives the overlay: a
 * neighbor to the left of a face draws tile 9 along the face's left edge.
 */
import { DIRECTIONS, FACE_EDGES } from './core.mjs';

// Edge bits: left 1, down 2, right 4, up 8 (texture space, seen from outside the face).
// Corner k lies between edge k and edge k + 1: left+down, down+right, right+up, up+left.
const EDGE_TILES = [[], [9], [1], [4], [7], [9, 7], [3], [5], [15], [11], [1, 15], [6], [10], [13], [12], [8]];
export const CORNER_TILES = Object.freeze([2, 0, 14, 16]);

/** World directions of the texture-space left, down, right and up neighbors of each face. */
export const OVERLAY_DIRECTIONS = Object.freeze(Object.fromEntries(Object.entries(FACE_EDGES).map(([face, [up, right, down, left]]) =>
  [face, Object.freeze([left, down, right, up])])));

const bitCount = mask => (mask & 1) + ((mask >> 1) & 1) + ((mask >> 2) & 1) + ((mask >> 3) & 1);

/** Corners whose two edges are both free of the overlay. */
export const freeCorners = edges => [0, 1, 2, 3].filter(corner => !(edges & ((1 << corner) | (1 << ((corner + 1) % 4)))));

/**
 * Tiles drawn for one face, in the order Java draws them: the edge tile (two
 * for opposite edges), then the corner tiles. `edges` and `corners` are the
 * masks overlaySelection returns.
 */
export function overlayTiles(edges, corners) {
  const tiles = [...EDGE_TILES[edges & 15]];
  // A single edge draws the corners that follow it; no edge draws them from left+down on.
  const start = bitCount(edges & 15) === 1 ? [1, 2, 4, 8].indexOf(edges & 15) + 1 : 0;
  for (let step = 0; step < 4; step++) {
    const corner = (start + step) % 4;
    if (corners & (1 << corner) && freeCorners(edges & 15).includes(corner)) tiles.push(CORNER_TILES[corner]);
  }
  return tiles;
}

/**
 * Which corners draw. A corner needs both of its edges free of the overlay, a
 * neighbor beside it that carries the same overlay rule (`same`, edge bits)
 * and its diagonal neighbor to give the overlay (cornerApplies(k), asked only
 * when the rest holds). OptiFine skips the neighbor check when two adjacent
 * edges have the overlay; Continuity keeps it.
 */
export function overlayCorners(edges, same, cornerApplies, dialect = 'continuity') {
  let corners = 0;
  const adjacentPair = bitCount(edges & 15) === 2 && (edges & 5) !== 5 && (edges & 10) !== 10;
  for (const corner of freeCorners(edges & 15)) {
    const sides = (1 << corner) | (1 << ((corner + 1) % 4));
    if (!(dialect === 'optifine' && adjacentPair) && !(same & sides)) continue;
    if (cornerApplies(corner)) corners |= 1 << corner;
  }
  return corners;
}

/** Every (edges, corners) result: 47 cases, sorted; index 0 is no overlay. */
export const OVERLAY_COMBOS = Object.freeze((() => {
  const combos = [];
  for (let edges = 0; edges < 16; edges++) {
    const free = freeCorners(edges);
    for (let subset = 0; subset < 1 << free.length; subset++) {
      let corners = 0;
      free.forEach((corner, index) => { if (subset & (1 << index)) corners |= 1 << corner; });
      combos.push(Object.freeze([edges, corners]));
    }
  }
  return combos.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
})());
const COMBO_INDEX = new Map(OVERLAY_COMBOS.map(([edges, corners], index) => [edges * 16 + corners, index]));

/** Index of an (edges, corners) result in OVERLAY_COMBOS. */
export const overlayComboIndex = (edges, corners) => COMBO_INDEX.get((edges & 15) * 16 + (corners & 15));

const add = (a, b) => ({ x: a.x + b.x, y: a.y + b.y, z: a.z + b.z });
const vector = name => { const [x, y, z] = DIRECTIONS[name]; return { x, y, z }; };

function query(block, delta) {
  try {
    return block.offset(delta);
  } catch (error) {
    return error?.name === 'LocationOutOfWorldBoundariesError' ? null : undefined;
  }
}

/**
 * The overlay on one face of `block`. For each texture-space neighbor in the
 * plane of the face: when the block in front of it is a solid render block, it
 * neither gives the overlay nor counts as carrying it; otherwise
 * `applies(neighbor)` says it gives the overlay and `same(neighbor)` that it
 * carries the same rule. A diagonal gives a corner when it applies and the
 * block in front of it is not solid. Returns {known: false} while a needed
 * neighbor is unloaded, else {known: true, edges, corners}.
 */
export function overlaySelection(block, face, { applies, same = () => false, solid = () => false, dialect = 'continuity' }) {
  const directions = OVERLAY_DIRECTIONS[face].map(vector), normal = vector(face);
  let edges = 0, carriers = 0;
  for (let edge = 0; edge < 4; edge++) {
    const front = query(block, add(directions[edge], normal));
    if (front === undefined) return { known: false };
    if (front && solid(front)) continue;
    const neighbor = query(block, directions[edge]);
    if (neighbor === undefined) return { known: false };
    if (!neighbor) continue;
    if (applies(neighbor)) edges |= 1 << edge;
    if (same(neighbor)) carriers |= 1 << edge;
  }
  let unknown = false;
  const corners = overlayCorners(edges, carriers, corner => {
    const delta = add(directions[corner], directions[(corner + 1) % 4]);
    const diagonal = query(block, delta);
    if (diagonal === undefined) { unknown = true; return false; }
    if (!diagonal || !applies(diagonal)) return false;
    const front = query(block, add(delta, normal));
    if (front === undefined) { unknown = true; return false; }
    return !(front && solid(front));
  }, dialect);
  return unknown ? { known: false } : { known: true, edges, corners };
}
