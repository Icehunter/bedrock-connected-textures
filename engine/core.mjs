/** Neighbor selection shared by potential Bedrock rendering adapters. */
export const DIRECTIONS = Object.freeze({
  north: [0, 0, -1], east: [1, 0, 0], south: [0, 0, 1],
  west: [-1, 0, 0], up: [0, 1, 0], down: [0, -1, 0],
});

// Image-space top, right, bottom, left viewed from outside each face.
export const FACE_EDGES = Object.freeze({
  north: ['up', 'west', 'down', 'east'],
  east: ['up', 'north', 'down', 'south'],
  south: ['up', 'east', 'down', 'west'],
  west: ['up', 'south', 'down', 'north'],
  up: ['north', 'east', 'south', 'west'],
  down: ['south', 'east', 'north', 'west'],
});

export function canonicalMask(raw) {
  let mask = raw & 15;
  for (let corner = 0; corner < 4; corner++) {
    const edges = (1 << corner) | (1 << ((corner + 1) % 4));
    if ((mask & edges) === edges && (raw & (1 << (corner + 4)))) {
      mask |= 1 << (corner + 4);
    }
  }
  return mask;
}

export const MASKS = Object.freeze([...new Set(
  Array.from({ length: 256 }, (_, mask) => canonicalMask(mask)),
)].sort((a, b) => a - b));

export function tileIndex(mask) {
  // Our mask ordering is explicit; it is not OptiFine's tile numbering.
  return MASKS.indexOf(canonicalMask(mask));
}

function offset(block, vector) {
  try {
    return block.offset({ x: vector[0], y: vector[1], z: vector[2] });
  } catch (error) {
    if (error?.name === 'LocationOutOfWorldBoundariesError') return null;
    return undefined;
  }
}

export function sameBlock(first, second) {
  return second !== null && second !== undefined && first.typeId === second.typeId;
}

export function selectFace(block, face, connects = sameBlock, options = {}) {
  const directions = FACE_EDGES[face];
  if (!directions) throw new Error(`Unknown face: ${face}`);
  const front = offset(block, DIRECTIONS[face]);
  if (front === undefined) return { known: false };
  if ((options.occludes ?? connects)(block, front)) return { known: true, visible: false };
  const connected = adjacent => {
    if (!connects(block, adjacent)) return false;
    if (!options.innerSeams) return true;
    const outside = offset(adjacent, DIRECTIONS[face]);
    if (outside === undefined) return undefined;
    return !connects(block, outside);
  };
  let raw = 0;
  for (let edge = 0; edge < 4; edge++) {
    const adjacent = offset(block, DIRECTIONS[directions[edge]]);
    if (adjacent === undefined) return { known: false };
    const connection = connected(adjacent);
    if (connection === undefined) return { known: false };
    if (connection) raw |= 1 << edge;
  }
  for (let corner = 0; corner < 4; corner++) {
    const first = DIRECTIONS[directions[corner]];
    const second = DIRECTIONS[directions[(corner + 1) % 4]];
    const edges = (1 << corner) | (1 << ((corner + 1) % 4));
    if (!options.allDiagonals && (raw & edges) !== edges) continue;
    const diagonal = offset(block, first.map((value, axis) => value + second[axis]));
    if (diagonal === undefined) return { known: false };
    const connection = connected(diagonal);
    if (connection === undefined) return { known: false };
    if (connection) raw |= 1 << (corner + 4);
  }
  const mask = canonicalMask(raw);
  return { known: true, visible: true, mask, ...(options.allDiagonals ? {rawMask:raw} : {}), tile: tileIndex(mask) };
}

export function selectBlock(block, connects = sameBlock) {
  return Object.fromEntries(Object.keys(FACE_EDGES).map(face => [face, selectFace(block, face, connects)]));
}

/**
 * A block as rules and terrain edges see it: a replacement block (a custom
 * block the engine swapped in for a vanilla one) reads as the vanilla block it
 * stands for, and every neighbor reached through it reads the same way.
 */
class VanillaBlock {
  constructor(block, typeId, view) { this.block = block; this.typeId = typeId; this.view = view; this.states = undefined; }
  get location() { return this.block.location; }
  get dimension() { return this.block.dimension; }
  get isAir() { return this.block.isAir; }
  get isLiquid() { return this.block.isLiquid; }
  get isValid() { return this.block.isValid; }
  /** True when the block in the world is a replacement standing for `typeId`. */
  get replaced() { return this.typeId !== this.block.typeId; }
  get permutation() {
    if (!this.replaced) return this.block.permutation;
    const states = this.states ??= this.view.statesOf(this.block);
    // Mirrored states drop the namespace ("minecraft:connection_east" is "bct:connection_east").
    return { getState: name => states[name] ?? states[name.split(':').pop()], getAllStates: () => ({ ...states }) };
  }
  offset(delta) { return this.view.wrap(this.block.offset(delta)); }
  above(steps) { return this.view.wrap(this.block.above(steps)); }
  below(steps) { return this.view.wrap(this.block.below(steps)); }
  getComponent(name) { return this.block.getComponent(name); }
  setPermutation(permutation) { return this.block.setPermutation(permutation); }
  setType(type) { return this.block.setType(type); }
}

/**
 * Wraps blocks so a replacement reads as its vanilla block. vanillaType maps a
 * block type to the vanilla type it stands for (the type itself for other
 * blocks); vanillaStates(block) gives a replacement's vanilla states. Without
 * it, states are read from the replacement's mirrored "bct:" states.
 */
export function vanillaView(vanillaType = typeId => typeId, vanillaStates = undefined) {
  const statesOf = block => {
    const own = vanillaStates?.(block);
    if (own) return own;
    const raw = block.permutation?.getAllStates?.() ?? {}, result = {};
    for (const [name, value] of Object.entries(raw)) result[name.startsWith('bct:') ? name.slice(4) : name] = value;
    return result;
  };
  const view = { statesOf, vanillaType,
    wrap: block => (!block || block instanceof VanillaBlock) ? block : new VanillaBlock(block, vanillaType(block.typeId), view) };
  return view;
}
