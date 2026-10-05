/**
 * Tile selection for the OptiFine connected-texture properties format:
 * ctm, horizontal, vertical, horizontal+vertical, vertical+horizontal, top,
 * fixed, random, repeat, the ctm_repeat and horizontal_repeat extensions and
 * the overlay family, plus rule matching and output chaining.
 *
 * Results: {known: false, reason} while a neighbor, state or provider is
 * missing; {known: true, visible: false} when the native face should show;
 * otherwise {known: true, visible: true, tile, orientation}.
 */
import { DIRECTIONS, FACE_EDGES, canonicalMask } from './core.mjs';
import { TILE_TEMPLATE } from './tile-template.mjs';
import { overlaySelection, overlayTiles } from './overlay.mjs';

const FACE_INDEX = { down: 0, up: 1, north: 2, south: 3, west: 4, east: 5 };
const FACE_NAMES = Object.keys(FACE_INDEX);
const SYMMETRY = { none: 1, opposite: 2, all: 6 };

/** Carrier entities sit 0.502 blocks out from the block center on each face. */
export const CARRIER_OFFSETS = Object.freeze(Object.fromEntries(Object.entries(DIRECTIONS).map(([face, d]) =>
  [face, Object.freeze(d.map(value => value * 0.502))])));

export function carrierPosition(location, face) {
  const [dx, dy, dz] = CARRIER_OFFSETS[face];
  const at = (base, delta) => Math.round((base + 0.5 + delta) * 1000) / 1000;
  return { x: at(location.x, dx), y: at(location.y, dy), z: at(location.z, dz) };
}

// Texture orientations 0-3 turn the texture by quarter turns, 4-7 mirror it
// first. Entry k is the world edge (FACE_EDGES order) seen as texture edge k
// (up, right, down, left).
const TEXTURE_EDGES = [[0, 1, 2, 3], [3, 0, 1, 2], [2, 3, 0, 1], [1, 2, 3, 0],
  [0, 3, 2, 1], [3, 2, 1, 0], [2, 1, 0, 3], [1, 0, 3, 2]];

/** Moves a world-space neighbor mask into texture space. */
export function orientMask(mask, orientation) {
  const edges = TEXTURE_EDGES[orientation];
  let result = 0;
  for (let k = 0; k < 4; k++) {
    if (mask & (1 << edges[k])) result |= 1 << k;
    const a = edges[k], b = edges[(k + 1) % 4];
    const corner = b === (a + 1) % 4 ? a : b;
    if (mask & (1 << (corner + 4))) result |= 1 << (k + 4);
  }
  return result;
}

// Pillar blocks lying along x or z show their texture turned on these faces.
const AXIS_ORIENTATION = {
  x: { east: 2, west: 2, up: 1, down: 1, north: 1, south: 1 },
  z: { east: 4, west: 4, up: 0, down: 0, north: 0, south: 0 },
};

/** Texture orientation implied by a pillar_axis state; undefined when the state is not an axis. */
export function axisOrientation(block, face) {
  const axis = block.permutation?.getState?.('pillar_axis');
  if (axis === undefined || axis === 'y') return 0;
  return AXIS_ORIENTATION[axis]?.[face];
}

const symmetricFace = (face, symmetry = 'none') => {
  const step = SYMMETRY[symmetry] ?? 1;
  return Math.floor(FACE_INDEX[face] / step) * step;
};
const modulo = (value, size) => ((value % size) + size) % size;

/**
 * Index into a width x height repeat pattern. Steps follow the texture's right
 * and down directions on each face, matching the Java coordinate formula.
 */
export function repeatIndex(location, face, width, height, symmetry = 'none', orientation = 0) {
  const { x, y, z } = location;
  const side = FACE_NAMES[symmetricFace(face, symmetry)];
  const [x0, y0] = {
    down: [x, -z - 1], up: [x, z], north: [-x - 1, -y],
    south: [x, -y], west: [z, -y], east: [-z - 1, -y],
  }[side];
  // Coordinate running along each base direction: up, right, down, left.
  const along = [-y0 - 1, x0, y0, -x0 - 1];
  const edges = TEXTURE_EDGES[orientation] ?? TEXTURE_EDGES[0];
  return modulo(along[edges[2]], height) * width + modulo(along[edges[1]], width);
}

// 64-bit position hash for random tiles (Continuity's mix).
const MASK64 = (1n << 64n) - 1n;
const GAMMA = 0x9e3779b97f4a7c15n;
const mix64 = (value, shiftA, multA, shiftB, multB, shiftC) => {
  value = ((value ^ (value >> shiftA)) * multA) & MASK64;
  value = ((value ^ (value >> shiftB)) * multB) & MASK64;
  return shiftC ? value ^ (value >> shiftC) : value;
};
const FACE_SEEDS = FACE_NAMES.map((_, face) => mix64((GAMMA * BigInt(face + 1)) & MASK64,
  30n, 0xbf58476d1ce4e5b9n, 27n, 0x94d049bb133111ebn, 31n));

/** Minecraft's per-position block seed (Mth.getSeed). */
export function javaBlockSeed({ x, y, z }) {
  const seed = BigInt(Math.imul(x, 3129871)) ^ (BigInt(z) * 116129781n) ^ BigInt(y);
  return BigInt.asIntN(64, seed * seed * 42317861n + seed * 11n) >> 16n;
}

/** Non-negative 31-bit random value for a block face. */
export function javaRandom(location, face, loops = 0, symmetry = 'none') {
  const side = symmetricFace(face, symmetry);
  let value = ((javaBlockSeed(location) ^ FACE_SEEDS[side]) + GAMMA * BigInt(loops + 1)) & MASK64;
  value = mix64(value, 33n, 0x62a9d9ed799705f5n, 28n, 0xcb24d0a5c88c35b3n, 0n);
  return Number((value >> 32n) & 0x7fffffffn);
}

// java.util.Random, enough for model selection.
const SEED_MASK = (1n << 48n) - 1n;
class JavaRandom {
  constructor(seed) { this.state = (BigInt.asUintN(64, seed) ^ 0x5deece66dn) & SEED_MASK; }
  next(bits) {
    this.state = (this.state * 0x5deece66dn + 0xbn) & SEED_MASK;
    return Number(BigInt.asIntN(32, this.state >> BigInt(48 - bits)));
  }
  nextInt(bound) {
    if ((bound & -bound) === bound) return Number((BigInt(bound) * BigInt(this.next(31))) >> 31n);
    for (;;) {
      const bits = this.next(31), value = bits % bound;
      if (bits - value + (bound - 1) <= 0x7fffffff) return value;
    }
  }
  nextLong() {
    const high = BigInt(this.next(32)), low = BigInt(this.next(32));
    return BigInt.asIntN(64, (high << 32n) + low);
  }
}

/** Weighted-model draw in [0, total) the way Java 26.2 picks block model variants. */
export function javaModelRandom(location, total, selection = 'java-26.2-block-position') {
  const random = new JavaRandom(javaBlockSeed(location));
  if (selection === 'java-26.2-block-position') return random.nextInt(total);
  if (selection === 'java-26.2-multipart-block-position') return new JavaRandom(random.nextLong()).nextInt(total);
  throw new Error('Unknown model selection: ' + selection);
}

const weightedIndex = (weights, draw) => {
  for (let index = 0; index < weights.length; index++) {
    if (draw < weights[index]) return index;
    draw -= weights[index];
  }
  return weights.length - 1;
};

export function javaModelIndex(location, weights, selection) {
  return weightedIndex(weights, javaModelRandom(location, weights.reduce((total, value) => total + value, 0), selection));
}

function query(block, delta) {
  try {
    return block.offset(delta);
  } catch (error) {
    return error?.name === 'LocationOutOfWorldBoundariesError' ? null : undefined;
  }
}
const vector = ([x, y, z]) => ({ x, y, z });
const add = (a, b) => ({ x: a.x + b.x, y: a.y + b.y, z: a.z + b.z });

const statesKey = block => {
  const states = block.permutation?.getAllStates?.() ?? {};
  return JSON.stringify(Object.keys(states).sort().map(name => [name, states[name]]));
};
const logicalOf = providers => providers.logicalBlockOf ?? (block => block.typeId);

function connector(rule, block, face, textureOf, currentTexture, providers) {
  const mode = rule.connect ?? (rule.matchTiles?.length ? 'tile' : 'block');
  if (mode === 'tile') return other => textureOf(other, face) === currentTexture;
  if (mode === 'state') {
    const key = statesKey(block);
    return other => other.typeId === block.typeId && statesKey(other) === key;
  }
  const logical = logicalOf(providers), own = logical(block);
  return other => logical(other) === own;
}

const UNKNOWN_NEIGHBOR = Object.freeze({ known: false, reason: 'neighbor_unloaded' });

/**
 * Texture-space neighbor mask: bits 0-3 for the up, right, down and left
 * edges, bits 4-7 for the diagonal after each edge. `corners` is 'none',
 * 'linked' (only between two connected edges) or 'all'.
 */
function neighborMask(block, face, connects, orientation, corners, innerSeams) {
  const world = FACE_EDGES[face].map(direction => vector(DIRECTIONS[direction]));
  const edges = TEXTURE_EDGES[orientation].map(index => world[index]);
  const normal = vector(DIRECTIONS[face]);
  const connected = delta => {
    const other = query(block, delta);
    if (other === undefined) return undefined;
    if (!other || !connects(other)) return false;
    if (!innerSeams) return true;
    const outside = query(block, add(delta, normal));
    if (outside === undefined) return undefined;
    return !(outside && connects(outside));
  };
  let mask = 0;
  for (let k = 0; k < 4; k++) {
    const value = connected(edges[k]);
    if (value === undefined) return undefined;
    if (value) mask |= 1 << k;
  }
  if (corners === 'none') return mask;
  for (let k = 0; k < 4; k++) {
    const both = (1 << k) | (1 << ((k + 1) % 4));
    if (corners === 'linked' && (mask & both) !== both) continue;
    const value = connected(add(edges[k], edges[(k + 1) % 4]));
    if (value === undefined) return undefined;
    if (value) mask |= 1 << (k + 4);
  }
  return mask;
}

const pairTile = (first, second) => [3, 2, 0, 1][(first ? 1 : 0) + (second ? 2 : 0)];
const horizontalTile = mask => pairTile(mask & 8, mask & 2);
const verticalTile = mask => pairTile(mask & 4, mask & 1);

// horizontal+vertical: a vertical neighbor only counts when it is not part of
// a horizontal run itself (both diagonals beside it are free), and vice versa.
function horizontalVerticalTile(mask) {
  if (mask & 10) return horizontalTile(mask);
  const up = (mask & 1) && !(mask & 0x90), down = (mask & 4) && !(mask & 0x60);
  return up && down ? 5 : up ? 4 : down ? 6 : 3;
}
function verticalHorizontalTile(mask) {
  if (mask & 5) return verticalTile(mask);
  const right = (mask & 2) && !(mask & 0x30), left = (mask & 8) && !(mask & 0xc0);
  return right && left ? 5 : right ? 4 : left ? 6 : 3;
}

function randomLocation(rule, block) {
  if (!rule.linked || block.permutation?.getState?.('upper_block_bit') !== true) return block.location;
  return { ...block.location, y: block.location.y - 1 };
}

function randomTile(rule, block, face) {
  const value = javaRandom(randomLocation(rule, block), face, rule.randomLoops ?? 0, rule.symmetry ?? 'none');
  if (rule.weights?.length) return weightedIndex(rule.weights, value % rule.weights.reduce((a, b) => a + b, 0));
  return value % Math.max(1, rule.tiles?.length ?? 1);
}

const MASK_CORNERS = { ctm: 'linked', ctm_repeat: 'linked', 'horizontal+vertical': 'all', 'vertical+horizontal': 'all' };

/** Tile index for one method; null when the native face should show, undefined while a neighbor is unloaded. */
function methodTile(rule, block, face, connects, orientation) {
  const method = rule.method ?? 'ctm';
  const area = (rule.width ?? 1) * (rule.height ?? 1);
  const pattern = () => repeatIndex(block.location, face, rule.width ?? 1, rule.height ?? 1, rule.symmetry, orientation);
  switch (method) {
    case 'fixed': return 0;
    case 'random': return randomTile(rule, block, face);
    case 'repeat': return pattern();
    case 'top': {
      if (face === 'up' || face === 'down') return null;
      const above = query(block, { x: 0, y: 1, z: 0 });
      if (above === undefined) return undefined;
      return above && connects(above) ? 0 : null;
    }
    case 'ctm': case 'ctm_repeat': case 'horizontal': case 'vertical': case 'horizontal_repeat':
    case 'horizontal+vertical': case 'vertical+horizontal': {
      const mask = neighborMask(block, face, connects, orientation, MASK_CORNERS[method] ?? 'none', rule.innerSeams);
      if (mask === undefined) return undefined;
      switch (method) {
        case 'ctm': return TILE_TEMPLATE[canonicalMask(mask)];
        case 'ctm_repeat': return TILE_TEMPLATE[canonicalMask(mask)] * area + pattern();
        case 'horizontal': return horizontalTile(mask);
        case 'vertical': return verticalTile(mask);
        case 'horizontal_repeat': return horizontalTile(mask) * area + pattern();
        case 'horizontal+vertical': return horizontalVerticalTile(mask);
        default: return verticalHorizontalTile(mask);
      }
    }
    default: throw new Error('Unsupported method: ' + method);
  }
}

function ruleOrientation(rule, block, face, providers) {
  if (rule.orient === 'state_axis') {
    const orientation = axisOrientation(block, face);
    return orientation === undefined ? { known: false, reason: 'state_axis_provider' } : orientation;
  }
  if (providers.orientationOf) {
    const orientation = providers.orientationOf(block, face);
    return orientation === undefined ? { known: false, reason: 'texture_orientation_provider' } : orientation;
  }
  return rule.orientation ?? 0;
}

/** Whether the face is covered; undefined while the neighbor in front is unloaded. */
function covered(block, face, providers) {
  const front = query(block, vector(DIRECTIONS[face]));
  if (front === undefined) return undefined;
  if (!front) return false;
  if (providers.faceOccludes) return !!providers.faceOccludes(block, front);
  const logical = logicalOf(providers);
  return logical(front) === logical(block) || !!providers.opaque?.(front);
}

export function chooseTile(rule, block, face, textureOf = b => b.typeId, currentTexture = textureOf(block, face), providers = {}) {
  const hidden = covered(block, face, providers);
  if (hidden === undefined) return UNKNOWN_NEIGHBOR;
  if (hidden) return { known: true, visible: false };
  const orientation = ruleOrientation(rule, block, face, providers);
  if (typeof orientation !== 'number') return orientation;
  const connects = connector(rule, block, face, textureOf, currentTexture, providers);
  const tile = methodTile(rule, block, face, connects, rule.orient === 'none' ? 0 : orientation);
  if (tile === undefined) return UNKNOWN_NEIGHBOR;
  if (tile === null) return { known: true, visible: false, orientation };
  return { known: true, visible: true, tile, orientation };
}

const stateMatches = (states, block, providers) => Object.entries(states ?? {}).every(([name, values]) => {
  const value = providers.stateOf ? providers.stateOf(block, name) : block.permutation?.getState?.(name);
  return (Array.isArray(values) ? values : [values]).map(String).includes(String(value));
});

/** true, false, or a {known: false} result when a provider is missing. */
function ruleMatches(rule, block, face, texture, providers) {
  if (rule.faces && !rule.faces.includes(face)) return false;
  if (rule.matchTiles?.length && !rule.matchTiles.includes(texture)) return false;
  const ids = [block.typeId, logicalOf(providers)(block)];
  if (rule.blocks?.length && !rule.blocks.some(id => ids.includes(id))) return false;
  if (rule.blockMatchers && !rule.blockMatchers.some(clause => ids.includes(clause.block) && stateMatches(clause.states, block, providers))) return false;
  if (rule.states && !stateMatches(rule.states, block, providers)) return false;
  if (rule.heights && !rule.heights.some(([low, high]) => block.location.y >= low && block.location.y <= high)) return false;
  if (rule.biomes) {
    const biome = providers.biomeOf?.(block);
    if (biome === undefined) return { known: false, reason: 'biome_provider' };
    if (rule.biomes.ids.includes(biome) === rule.biomes.exclude) return false;
  }
  return true;
}

/**
 * The tiles an overlay rule picks for a face, or a {known: false} result.
 * Java draws overlay tiles unturned; overlay_ctm and overlay_repeat follow the
 * face orientation only when the rule sets `orient`.
 */
export function overlayRuleTiles(rule, block, face, textureOf, texture, providers) {
  if (rule.method === 'overlay') {
    if (!providers.opaque) return { known: false, reason: 'overlay_geometry_provider' };
    const logical = logicalOf(providers), dialect = providers.dialect ?? 'continuity';
    const full = providers.fullCube ?? providers.opaque;
    // A neighbor that connects with this block (same block or texture) never gives it the overlay.
    const connects = connector(rule, block, face, textureOf, texture, providers);
    const applies = other => full(other) && !connects(other) &&
      (!rule.connectBlocks || rule.connectBlocks.includes(logical(other)) || rule.connectBlocks.includes(other.typeId)) &&
      (!rule.connectTiles || rule.connectTiles.includes(textureOf(other, face)));
    // A neighbor carries the same overlay when the rule matches it as well; OptiFine compares its texture with this face's.
    const same = other => (rule.matchTiles?.length ? (dialect === 'optifine' ? textureOf(other, face) === texture : rule.matchTiles.includes(textureOf(other, face)))
      : (rule.blocks ?? []).some(id => id === other.typeId || id === logical(other)));
    const around = overlaySelection(block, face, { applies, same, solid: providers.opaque, dialect });
    if (!around.known) return UNKNOWN_NEIGHBOR;
    return overlayTiles(around.edges, around.corners);
  }
  const selected = chooseTile({ ...rule, method: rule.method.slice('overlay_'.length), orient: rule.orient ?? 'none' }, block, face, textureOf, texture, providers);
  if (!selected.known) return selected;
  return selected.visible ? [selected.tile] : [];
}

/** Layers an overlay rule adds to a visible face, or a {known: false} result. */
function overlayLayers(rule, block, face, textureOf, texture, providers) {
  const tiles = overlayRuleTiles(rule, block, face, textureOf, texture, providers), orientation = 0;
  if (!Array.isArray(tiles)) return tiles;
  let tint;
  if ('tintIndex' in rule || 'tintBlock' in rule) {
    tint = providers.tintOf?.(block, rule.tintBlock ?? logicalOf(providers)(block), rule.tintIndex ?? -1);
    if (!tint) return { known: false, reason: 'overlay_tint_provider' };
  }
  return tiles.map(tile => ({ rule, tile, texture: rule.tiles[tile], orientation, ...(tint ? { tint } : {}) }))
    .filter(layer => layer.texture !== '<skip>' && layer.texture !== '<default>');
}

const MAX_PASSES = 3;

/**
 * Resolver for one rule set. Rules matching the face texture (matchTiles) come
 * before rules matching the block; within each group a higher `weight` wins,
 * then file order. Each output texture is matched again against texture rules,
 * and each rule applies at most once. '<skip>' moves on to the next rule and
 * '<default>' keeps the native face. Overlay rules add layers on top.
 */
export function ruleResolver(rules, providers = {}) {
  const ordered = rules.map((rule, index) => ({ rule, index }))
    .sort((a, b) => (b.rule.weight ?? 0) - (a.rule.weight ?? 0) || a.index - b.index).map(entry => entry.rule);
  const overlays = ordered.filter(rule => rule.method?.startsWith('overlay'));
  const byTexture = new Map(), byBlock = [];
  for (const rule of ordered) {
    if (rule.method?.startsWith('overlay')) continue;
    if (!rule.matchTiles?.length) { byBlock.push(rule); continue; }
    for (const texture of rule.matchTiles) {
      if (!byTexture.has(texture)) byTexture.set(texture, []);
      byTexture.get(texture).push(rule);
    }
  }
  return (block, face, textureOf = b => b.typeId) => {
    const original = textureOf(block, face);
    const hidden = covered(block, face, providers);
    if (hidden === undefined) return UNKNOWN_NEIGHBOR;
    if (hidden) return { known: true, visible: false, overlays: [] };
    const layers = [];
    for (const rule of overlays) {
      const matched = ruleMatches(rule, block, face, original, providers);
      if (matched !== true) { if (matched) return matched; continue; }
      const added = overlayLayers(rule, block, face, textureOf, original, providers);
      if (!Array.isArray(added)) return added;
      layers.push(...added);
    }
    let result = { known: true, visible: false }, texture = original;
    const applied = new Set();
    for (let pass = 0; pass < MAX_PASSES; pass++) {
      const candidates = [...(byTexture.get(texture) ?? []), ...(pass === 0 ? byBlock : [])];
      let advanced = false;
      for (const rule of candidates) {
        if (applied.has(rule)) continue;
        const matched = ruleMatches(rule, block, face, texture, providers);
        if (matched !== true) { if (matched) return matched; continue; }
        applied.add(rule);
        const selected = chooseTile(rule, block, face, textureOf, texture, providers);
        if (!selected.known) return selected;
        if (!selected.visible) return { ...selected, rule, texture, overlays: layers };
        const output = rule.tiles?.[selected.tile];
        if (output === '<skip>') continue;
        if (output === '<default>') return { known: true, visible: false, rule, texture: original, overlays: layers };
        result = { ...selected, rule, texture: output };
        texture = output;
        advanced = true;
        break;
      }
      if (!advanced) break;
    }
    return { ...result, overlays: layers };
  };
}
