// The tile each world-independent CTM rule shows on a lone block, for the
// native previews (converter/java_native_fallbacks.py). Reads
// {rules, baseTextures, layerTextures} on stdin and writes
// [{block, face, source, replacement, rule}] for every face a rule changes.
import {readFileSync} from 'node:fs';
import {ruleResolver} from '../engine/tiles.mjs';

const AIR = 'minecraft:air';
const input = JSON.parse(readFileSync(0, 'utf8'));
// Native base images cannot depend on a world, state, or biome. The runtime
// still receives every original rule and resolves those conditions in-game.
const worldIndependentRules = input.rules.filter(rule => !rule.method.startsWith('overlay') &&
  !rule.biomes && !rule.heights && !rule.states && !rule.blockMatchers);
const isSolid = block => block.typeId !== AIR;
const resolveTile = ruleResolver(worldIndependentRules, {orientationOf: () => 0, fullCube: isSolid, opaque: isSolid});

// The base faces of every block, then each face layer of its models on its own.
const blockFaces = [...Object.entries(input.baseTextures),
  ...(input.layerTextures ?? []).map(layer => [layer.block, {[layer.face]: layer.texture}])];
const replacements = [];
for (const [typeId, faces] of blockFaces) {
  // The block alone at the origin, with air on every side.
  const blockAt = (location, isOrigin = false) => ({
    typeId: isOrigin ? typeId : AIR,
    location,
    permutation: {getState: () => undefined, getAllStates: () => ({})},
    offset: delta => blockAt(Object.fromEntries(['x', 'y', 'z'].map(axis =>
      [axis, location[axis] + (delta[axis] ?? 0)]))),
  });
  const block = blockAt({x: 0, y: 0, z: 0}, true);
  const textureOf = (other, face) => other.typeId === typeId ? faces[face] : AIR;
  for (const [face, source] of Object.entries(faces)) {
    const selected = resolveTile(block, face, textureOf);
    if (selected.known && selected.visible && selected.texture !== source) {
      replacements.push({block: typeId, face, source, replacement: selected.texture, rule: selected.rule.id});
    }
  }
}
process.stdout.write(JSON.stringify(replacements));
