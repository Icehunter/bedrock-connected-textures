import { DIRECTIONS, selectFace } from './core.mjs';
import { grassTint } from './tint.mjs';

export function validateProvider(provider) {
  if (provider.format_version !== 1 || !/^[a-z][a-z0-9_]*$/.test(provider.id)) throw new Error('Invalid provider header');
  if (!provider.effects || !Array.isArray(provider.rules) || provider.rules.length > 64) throw new Error('Invalid provider rules');
  if (Object.keys(provider.effects).length > 16) throw new Error('Effect limit reached');
  if (provider.biome_palette !== undefined && (!Array.isArray(provider.biome_palette) || !provider.biome_palette.length || provider.biome_palette.length > 256 || provider.biome_palette.some(color => !Array.isArray(color) || color.length !== 3 || color.some(value => typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1)))) throw new Error('Invalid biome palette');
  const ids = new Set();
  for (const [name, effect] of Object.entries(provider.effects)) {
    if (!/^[a-z][a-z0-9_]*$/.test(name) || !['surface', 'model'].includes(effect.kind)) throw new Error('Invalid effect');
    if (!/^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$/.test(effect.entity)) throw new Error('Invalid effect entity');
    if (effect.native_material !== undefined && (!effect.native_block || !Number.isInteger(effect.native_material) || effect.native_material < 1 || effect.native_material > 3)) throw new Error('Invalid native material');
    if (effect.native_block !== undefined && !/^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$/.test(effect.native_block)) throw new Error('Invalid native surface block');
    if (effect.native_disabled !== undefined && typeof effect.native_disabled !== 'boolean') throw new Error('Invalid native disable flag');
    if (effect.entity_surface !== undefined && (typeof effect.entity_surface !== 'boolean' || effect.kind !== 'surface')) throw new Error('Invalid entity surface flag');
    if (effect.height_tuning !== undefined && (effect.height_tuning !== true || !effect.entity_surface || !Number.isInteger(effect.priority) || effect.priority < 1 || effect.priority > 5)) throw new Error('Invalid height tuning');
    if (effect.height_profiles !== undefined && (!Array.isArray(effect.height_profiles) || effect.height_profiles.length !== 2 || effect.height_profiles[0] !== 1 || effect.height_profiles[1] !== 2)) throw new Error('Invalid height profiles');
    if (effect.native_offset !== undefined && (![1,2].includes(effect.native_offset) || !effect.native_block)) throw new Error('Invalid native offset');
    if (effect.priority !== undefined && (!Number.isInteger(effect.priority) || effect.priority < 0 || effect.priority > 15)) throw new Error('Invalid material priority');
    if(effect.mask_lookup !== undefined && (!Array.isArray(effect.mask_lookup) || effect.mask_lookup.length!==216 || effect.mask_lookup.some(value=>!Number.isInteger(value)||value<0||value>19))) throw new Error('Invalid quarter mask lookup');
    if (effect.layer !== undefined && (!Number.isInteger(effect.layer) || effect.layer < 0 || effect.layer > 15)) throw new Error('Invalid material layer');
  }
  for (const rule of provider.rules) {
    if (!/^[a-z][a-z0-9_]*$/.test(rule.id) || ids.has(rule.id)) throw new Error('Invalid or duplicate rule ID');
    ids.add(rule.id);
    for (const name of ['targets', 'neighbors', 'contacts']) {
      if (!Array.isArray(rule[name]) || !rule[name].length || rule[name].length > 128) throw new Error(`Invalid ${name}`);
    }
    for (const type of [...rule.targets, ...rule.neighbors]) {
      if (typeof type !== 'string' || !/^[a-z][a-z0-9_]*:[a-z][a-z0-9_]*$/.test(type)) throw new Error('Invalid block ID');
    }
    if (rule.contacts.some(direction => !DIRECTIONS[direction])) throw new Error('Invalid contact direction');
    if (rule.neighbor_y !== undefined && (!Number.isInteger(rule.neighbor_y) || Math.abs(rule.neighbor_y) > 1)) throw new Error('Invalid neighbor height');
    const effect = provider.effects[rule.effect];
    if (!effect) throw new Error('Unknown effect');
    if (rule.selection !== undefined && rule.selection !== 'neighbor_overlay') throw new Error('Invalid selection');
    if (rule.biome_tint !== undefined && rule.biome_tint !== 'grass') throw new Error('Invalid biome tint');
    if (rule.corner_policy !== undefined && !['edge_contact', 'path_corner'].includes(rule.corner_policy)) throw new Error('Invalid corner policy');
    if (rule.selection === 'neighbor_overlay' && (effect.kind !== 'surface' || rule.faces?.length !== 1 || rule.faces[0] !== 'up' || rule.neighbor_y)) throw new Error('Neighbor overlays require a coplanar top surface');
    if (effect.kind === 'surface' && (!rule.faces?.length || rule.faces.some(face => !DIRECTIONS[face]))) throw new Error('Invalid surface faces');
    if (effect.kind === 'model' && rule.contacts.some(face => ['up', 'down'].includes(face))) throw new Error('Model contacts must be horizontal');
  }
  return provider;
}

function read(block, direction, height = 0) {
  const vector = DIRECTIONS[direction];
  try { return block.offset({ x: vector[0], y: vector[1] + height, z: vector[2] }); }
  catch (error) { return error?.name === 'LocationOutOfWorldBoundariesError' ? null : undefined; }
}

/**
 * null means a relevant neighbor is unknown; retain its existing visuals.
 * `open` holds more block types that leave a face uncovered (overlay surfaces).
 */
export function evaluate(provider, block, open = undefined) {
  const outputs = [];
  const passable = other => other?.isAir || !!open?.has(other?.typeId) || Object.values(provider.effects).some(effect => effect.native_block === other?.typeId);
  for (const rule of provider.rules) {
    if (!rule.targets.includes(block.typeId)) continue;
    if (rule.selection === 'neighbor_overlay') {
      const front = read(block, 'up');
      if (front === undefined) return null;
      if (!passable(front)) continue;
      const vectors = [[0,0,-1], [1,0,0], [0,0,1], [-1,0,0], [1,0,-1], [1,0,1], [-1,0,1], [-1,0,-1]];
      let mask = 0;
      const tintSum = [0, 0, 0];
      let tintWeight = 0;
      const neighbors = [];
      const adjacent = [];
      for (let index = 0; index < vectors.length; index++) {
        let neighbor;
        const [x, y, z] = vectors[index];
        try { neighbor = block.offset({x, y, z}); }
        catch (error) { if (error?.name !== 'LocationOutOfWorldBoundariesError') return null; }
        adjacent[index] = neighbor;
        if (rule.neighbors.includes(neighbor?.typeId)) {
          let above;
          try { above = neighbor.offset({x:0,y:1,z:0}); }
          catch (error) { if (error?.name !== 'LocationOutOfWorldBoundariesError') return null; }
          if (above === undefined) return null;
          if (!passable(above)) continue;
          mask |= 1 << index;
          neighbors[index] = neighbor;
        }
      }
      if (rule.corner_policy) {
        for (let corner = 0; corner < 4; corner++) {
          const edges = (1 << corner) | (1 << ((corner + 1) % 4));
          const bit = 1 << (corner + 4);
          if (!(mask & bit)) continue;
          // A corner cutout is contained in either adjacent edge cutout.
          // Drawing both adds overlapping faces without extending the blend.
          if (mask & edges) { mask &= ~bit; continue; }
          let supported = rule.corner_policy === 'path_corner';
          for (const side of [corner,(corner+1)%4]) {
            const bridge = adjacent[side];
            if (!rule.targets.includes(bridge?.typeId)) { supported = false; break; }
            let above;
            try { above = bridge.offset({x:0,y:1,z:0}); }
            catch (error) { if (error?.name !== 'LocationOutOfWorldBoundariesError') return null; }
            if (above === undefined) return null;
            if (!passable(above)) { supported = false; break; }
          }
          if (!supported) mask &= ~bit;
        }
      }
      if (rule.biome_tint === 'grass' && (!provider.effects[rule.effect].native_block || provider.effects[rule.effect].entity_surface)) {
        for (let index = 0; index < neighbors.length; index++) {
          if (!(mask & (1 << index))) continue;
          const tint = grassTint(neighbors[index], provider.biome_colors);
          if (!tint) return null;
          const weight = index < 4 ? 2 : 1;
          tint.forEach((value, channel) => { tintSum[channel] += value * weight; });
          tintWeight += weight;
        }
      }
      if (mask) outputs.push({provider: provider.id, rule: rule.id, entity: provider.effects[rule.effect].entity, kind: 'surface', face: 'up', tile: mask,
        ...(tintWeight ? {tint: tintSum.map(value => value / tintWeight)} : {})});
      // Each material keeps its own mask; provider geometry separates layers.
      continue;
    }
    const contacts = [];
    for (const direction of rule.contacts) {
      const neighbor = read(block, direction, rule.neighbor_y ?? 0);
      if (neighbor === undefined) return null;
      if (neighbor && rule.neighbors.includes(neighbor.typeId)) contacts.push(direction);
    }
    if (!contacts.length) continue;
    if (rule.air_above) {
      const above = read(block, 'up');
      if (above === undefined) return null;
      if (!above?.isAir && !open?.has(above?.typeId)) continue;
    }
    const effect = provider.effects[rule.effect];
    if (effect.kind === 'model') {
      // One model per anchor prevents overlapping roots at crowded corners.
      outputs.push({ provider: provider.id, rule: rule.id, entity: effect.entity,
        kind: 'model', face: contacts[0], tile: 0 });
      continue;
    }
    for (const face of rule.faces) {
      const front = read(block, face);
      if (front === undefined) return null;
      if (!front?.isAir && !open?.has(front?.typeId)) continue;
      const selected = selectFace(block, face, (_, other) => rule.targets.includes(other?.typeId));
      if (!selected.known) return null;
      if (selected.visible) outputs.push({ provider: provider.id, rule: rule.id, entity: effect.entity,
        kind: 'surface', face, tile: selected.tile });
    }
  }
  return outputs;
}

export function descriptorKey(dimension, location, output) {
  return `${dimension}|${location.x},${location.y},${location.z}|${output.provider}:${output.rule}|${output.face}`;
}
