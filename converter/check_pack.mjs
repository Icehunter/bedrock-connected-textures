/**
 * Loads a pack's BCT data the way its main.js does (scripts/bct.js with the built overlays and carriers) and
 * compiles it with the engine's own code, so `python bct.py check` reports what players would see in chat.
 *
 *   node converter/check_pack.mjs <behavior pack> <bedrock-samples>
 *
 * Prints one JSON line: { data, error } (data: what bct.js exports; error: the engine's message, or null).
 * The game's block states come from bedrock-samples and the pack's own blocks/ files.
 */
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { compileAuthored } from '../engine/authored.mjs';

const [bp, samples] = process.argv.slice(2);

/** {block: {state: default value}} for the vanilla blocks and the pack's own. */
function blockStates() {
  const states = new Map();
  const vanilla = JSON.parse(readFileSync(join(samples, 'metadata/vanilladata_modules/mojang-blocks.json'), 'utf8'));
  const values = new Map(vanilla.block_properties.map(property => [property.name, property.values.map(item => item.value)]));
  for (const item of vanilla.data_items)
    states.set(item.name, Object.fromEntries((item.properties ?? []).map(property => [property.name, values.get(property.name)?.[0]])));
  const folder = join(bp, 'blocks');
  const files = [];
  const walk = dir => { for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isDirectory()) walk(join(dir, entry.name)); else if (entry.name.endsWith('.json')) files.push(join(dir, entry.name)); } };
  if (existsSync(folder)) walk(folder);
  for (const file of files) {
    let block;
    try { block = JSON.parse(readFileSync(file, 'utf8'))['minecraft:block']; } catch { continue; }
    const description = block?.description;
    if (!description?.identifier) continue;
    states.set(description.identifier, Object.fromEntries(Object.entries(description.states ?? {}).map(([name, spec]) => {
      const options = Array.isArray(spec) ? spec : spec?.values;
      return [name, Array.isArray(options) ? options[0] : options?.min];
    })));
  }
  return states;
}

async function load(name) {
  const path = join(bp, 'scripts', name);
  if (!existsSync(path)) return undefined;
  return (await import(pathToFileURL(path).href)).default;
}

let output;
try {
  const data = await load('bct.js');
  const overlaySurfaces = await load('bct-overlays.js'), carrierSurfaces = await load('bct-carriers.js');
  const states = blockStates();
  const api = { BlockPermutation: { resolve(type) {
    if (!states.has(type)) throw new Error('Unknown block ' + type);
    return { getAllStates: () => ({ ...states.get(type) }) };
  } } };
  let error = null;
  try { compileAuthored(api, { ...data, overlaySurfaces, carrierSurfaces }); }
  catch (problem) { error = String(problem?.message ?? problem); }
  output = { data, error };
} catch (problem) {
  output = { data: null, error: 'scripts/bct.js could not be loaded: ' + String(problem?.message ?? problem) };
}
process.stdout.write(JSON.stringify(output) + '\n');
