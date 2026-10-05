// Reference for tests: the overlay method as Continuity's StandardOverlayQuadProcessor
// picks its sprites, ported case by case and kept apart from the engine's code.
import { OVERLAY_DIRECTIONS } from '../engine/overlay.mjs';
import { DIRECTIONS } from '../engine/core.mjs';

const add = (a, b) => a.map((value, axis) => value + b[axis]);

/**
 * Overlay tiles for one face, in drawing order. `type(vector)` names the block at
 * an offset ([x, y, z]) from the face's block: 'src' gives the overlay, 'host'
 * carries the same rule, 'solid' is a solid render block that covers whatever is
 * behind it; anything else is neither. The 'optifine' dialect draws the corner
 * opposite two adjacent edges without asking for a block with the same rule.
 */
export function referenceOverlayTiles(type, face, dialect = 'continuity') {
  const directions = OVERLAY_DIRECTIONS[face].map(name => DIRECTIONS[name]), normal = DIRECTIONS[face];
  const solid = vector => type(vector) === 'solid';
  const appearance = directions.map(direction => solid(add(direction, normal)) ? null : type(direction));
  const same = other => other === 'host';
  const cornerApplies = (a, b) => {
    const diagonal = add(directions[a], directions[b]);
    return type(diagonal) === 'src' && !solid(add(diagonal, normal));
  };
  const twoSides = (s0, s1, d0, d1, sprite, corner) => {
    const result = [sprite];
    if ((dialect === 'optifine' || same(appearance[s0]) || same(appearance[s1])) && cornerApplies(d0, d1)) result.push(corner);
    return result;
  };
  const oneSide = (s0, s1, s2, d0, d1, d2, sprite, c01, c12) => {
    let first, second;
    if (same(appearance[s1])) first = second = true;
    else { first = same(appearance[s0]); second = same(appearance[s2]); }
    const result = [sprite];
    if (first && cornerApplies(d0, d1)) result.push(c01);
    if (second && cornerApplies(d1, d2)) result.push(c12);
    return result;
  };
  const applications = appearance.reduce((mask, other, index) => mask | (other === 'src' ? 1 << index : 0), 0);
  switch (applications) {
    case 0b1111: return [8];
    case 0b0111: return [5];
    case 0b1011: return [6];
    case 0b1101: return [13];
    case 0b1110: return [12];
    case 0b0101: return [9, 7];
    case 0b1010: return [1, 15];
    case 0b0011: return twoSides(2, 3, 2, 3, 4, 14);
    case 0b0110: return twoSides(3, 0, 3, 0, 3, 16);
    case 0b1100: return twoSides(0, 1, 0, 1, 10, 2);
    case 0b1001: return twoSides(1, 2, 1, 2, 11, 0);
    case 0b0001: return oneSide(1, 2, 3, 1, 2, 3, 9, 0, 14);
    case 0b0010: return oneSide(2, 3, 0, 2, 3, 0, 1, 14, 16);
    case 0b0100: return oneSide(3, 0, 1, 3, 0, 1, 7, 16, 2);
    case 0b1000: return oneSide(0, 1, 2, 0, 1, 2, 15, 2, 0);
    default: {
      const s = appearance.map(same), result = [];
      if ((s[0] || s[1]) && cornerApplies(0, 1)) result.push(2);
      if ((s[1] || s[2]) && cornerApplies(1, 2)) result.push(0);
      if ((s[2] || s[3]) && cornerApplies(2, 3)) result.push(14);
      if ((s[3] || s[0]) && cornerApplies(3, 0)) result.push(16);
      return result;
    }
  }
}
