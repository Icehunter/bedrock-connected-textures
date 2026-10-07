/** Terrain transitions: grass and sand edges drawn onto neighboring blocks. */
import { NativeSurfaces } from './terrain-native.mjs';
import { evaluate, validateProvider, descriptorKey } from './terrain-rules.mjs';
import { createHeightSettings, DEFAULT_HEIGHTS } from './heights.mjs';
import { writeProperties } from './settings.mjs';
import { traceSurface } from './trace.mjs';
import { vanillaView } from './core.mjs';

const FACES = ['north', 'east', 'south', 'west', 'up', 'down'];
const TAG = 'bct_terrain';
const REVISION = 1;

function validateProviders(supplied) {
  if (!Array.isArray(supplied)) throw new Error('Terrain sources must be an array');
  const ids = new Set(), entities = new Set(), nativeBlocks = new Set();
  return supplied.map(source => {
    const provider = validateProvider(source);
    if (ids.has(provider.id)) throw new Error('Duplicate terrain source: ' + provider.id);
    ids.add(provider.id);
    for (const effect of Object.values(provider.effects)) {
      if (entities.has(effect.entity)) throw new Error('Duplicate terrain carrier: ' + effect.entity);
      entities.add(effect.entity);
    }
    for (const block of new Set(Object.values(provider.effects).map(effect => effect.native_block).filter(Boolean))) {
      if (nativeBlocks.has(block)) throw new Error('Duplicate terrain native block: ' + block);
      nativeBlocks.add(block);
    }
    return provider;
  });
}

/**
 * Terrain transition carriers. `limits()` returns the current terrain
 * settings; `inReach(dimensionId, location)` says whether a host is inside the
 * active area. A replacement block counts as the vanilla block it stands for
 * (`vanillaType`, `vanillaStates`), as a host and as a source, and
 * `aliasesOf` gives the replacement types the scanner also looks for.
 * `passable()` gives block types that leave a face uncovered like air (overlay surfaces).
 */
export function createTerrain({ api, limits, inReach = () => true, log = () => {}, providers: supplied = [],
  vanillaType = typeId => typeId, aliasesOf = () => [], vanillaStates = undefined, passable = () => undefined }) {
  const { world, system } = api;
  const view = vanillaView(vanillaType, vanillaStates);
  const providers = validateProviders(supplied);
  const owned = new Map(), hosts = new Map(), failures = new Map(), pending = new Map();
  const hostKey = (dimension, location) => dimension + ':' + location.x + ':' + location.y + ':' + location.z;
  const settings = createHeightSettings({ ModalFormData: api.ModalFormData, applyHeights, report: log });
  const native = new NativeSurfaces(world, (type, states) => api.BlockPermutation.resolve(type, states), () => providers,
    () => limits().maxNativeCarriers, () => system.currentTick, log, undefined, true);
  let enabled = true, recovered = false, suspended = false, recoveryPending = false, flushing = false;
  let passes = 0, capacitySkips = 0, validationCursor = 0;

  function remember(key, record) {
    owned.set(key, record);
    const id = hostKey(record.dimension, record);
    if (!hosts.has(id)) hosts.set(id, new Set());
    hosts.get(id).add(key);
  }

  const effectOf = output => Object.values(providers.find(provider => provider.id === output.provider).effects).find(effect => effect.entity === output.entity);

  function actorPosition(record, heights) {
    const effect = providers.flatMap(provider => Object.values(provider.effects)).find(effect => effect.entity === record.entity.typeId);
    if (!effect?.entity_surface || !effect.height_tuning) return null;
    const delta = (effect.mask_lookup ? 1 : heights[effect.priority - 1]) / 256;
    return { x: record.x + .5, y: record.y + 1 + delta, z: record.z + .5 };
  }

  function positionActor(record, heights) {
    const target = actorPosition(record, heights);
    if (target && ['x', 'y', 'z'].some(axis => Math.abs(record.entity.location[axis] - target[axis]) > 1e-6)) record.entity.teleport(target);
  }

  function applyHeights(heights) {
    if (!Array.isArray(heights) || heights.length !== DEFAULT_HEIGHTS.length || heights.some((value, index) => !Number.isFinite(value) || value < .25 || value > 16 || (index > 0 && value <= heights[index - 1]))) throw new Error('Invalid tier heights');
    const changes = [];
    for (const record of owned.values()) {
      if (!record.entity.isValid) continue;
      const after = actorPosition(record, heights);
      if (!after) continue;
      const before = { ...record.entity.location };
      if (['x', 'y', 'z'].every(axis => Math.abs(before[axis] - after[axis]) <= 1e-6)) continue;
      changes.push({ apply: () => record.entity.teleport(after), undo: () => record.entity.teleport(before) });
    }
    const completed = [];
    try {
      for (const change of changes) { change.apply(); completed.push(change); }
    } catch (error) {
      for (const change of completed.reverse()) {
        try { change.undo(); } catch (rollback) { log('height reset failed: ' + rollback); }
      }
      throw error;
    }
  }

  /** Refreshes the 3x3x3 neighborhood of a placed, broken or exploded block. */
  function changed(block) {
    if (!enabled || suspended || !block) return;
    const dimension = block.dimension, { x, y, z } = block.location;
    // The after-event can remove the changed anchor before deferred neighbor work.
    update(dimension, { x, y, z });
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
      const location = { x: x + dx, y: y + dy, z: z + dz };
      if (pending.size < 256) pending.set(dimension.id + ':' + Object.values(location).join(','), { dimension, location });
    }
    if (!flushing) { flushing = true; system.run(flushChanges); }
  }

  function flushChanges() {
    if (!enabled || suspended) pending.clear();
    let count = 0;
    for (const [key, entry] of pending) {
      pending.delete(key);
      update(entry.dimension, entry.location);
      if (++count >= 16) break;
    }
    if (pending.size) system.run(flushChanges);
    else flushing = false;
  }

  function remove(key, record) {
    try {
      if (record.entity.isValid) {
        record.entity.setProperty('bct:active', false);
        record.entity.setProperty('bct:tile', 0);
        record.entity.setDynamicProperty('bct:retiring', true);
        // Send the hidden state before removing a static cloud's server actor.
        system.runTimeout(() => {
          try { if (record.entity.isValid) record.entity.remove(); }
          catch (error) { log('carrier removal failed: ' + error); }
        }, 2);
      }
    }
    catch { return; } // Keep ownership so a failed removal can be retried.
    owned.delete(key);
    const id = hostKey(record.dimension, record);
    hosts.get(id)?.delete(key);
    if (!hosts.get(id)?.size) hosts.delete(id);
  }

  function validateAnchors() {
    const checked = new Set(), records = [...owned.values()];
    for (let work = 0; work < Math.min(records.length, 16); work++) {
      const record = records[validationCursor++ % records.length];
      const key = record.dimension + ':' + [record.x, record.y, record.z].join(',');
      if (checked.has(key)) continue;
      checked.add(key);
      const dimension = world.getDimension(record.dimension), location = { x: record.x, y: record.y, z: record.z };
      try {
        const block = view.wrap(dimension.getBlock(location));
        if (block && (block.typeId !== record.type || block.offset({ x: 0, y: 1, z: 0 })?.isAir === false)) update(dimension, location);
      } catch { /* Keep visuals while their chunk is unavailable. */ }
    }
  }

  /** Adopts saved carriers after a reload and removes stale or duplicate ones. */
  function recover() {
    if (!providers.length) return;
    const activeTypes = new Set(providers.flatMap(provider => Object.values(provider.effects).filter(effect => !effect.native_block || effect.entity_surface).map(effect => effect.entity)));
    for (const name of ['overworld', 'nether', 'the_end']) {
      for (const entity of [...activeTypes].flatMap(type => world.getDimension(name).getEntities({ type }))) {
        if (!entity.hasTag(TAG)) continue;
        if (suspended || entity.getDynamicProperty('bct:revision') !== REVISION || entity.getDynamicProperty('bct:retiring')) { remove('', { entity }); continue; }
        const key = entity.getDynamicProperty('bct:key'), anchor = entity.getDynamicProperty('bct:anchor');
        if (typeof key !== 'string' || typeof anchor !== 'string') { remove('', { entity }); continue; }
        if (owned.has(key)) {
          if (!owned.get(key).entity.isValid) owned.delete(key);
          else { if (owned.get(key).entity.id !== entity.id) remove('', { entity }); else owned.get(key).entity = entity; continue; }
        }
        try {
          const record = { entity, ...JSON.parse(anchor) };
          if (!inReach(record.dimension, record)) { remove('', { entity }); continue; }
          positionActor(record, settings.heights);
          remember(key, record);
        }
        catch { remove('', { entity }); }
      }
    }
  }

  function removeHost(keys) {
    for (const key of keys) {
      const record = owned.get(key);
      if (record) remove(key, record);
    }
  }

  function update(dimension, location) {
    if (!enabled || suspended) return;
    let block;
    try { block = view.wrap(dimension.getBlock(location)); } catch { return; }
    if (!block) return;
    if (native.ids().has(block.typeId)) {
      update(dimension, { x: location.x, y: location.y - native.offset(block.typeId), z: location.z });
      return;
    }
    const wanted = [], open = passable();
    for (const provider of providers) {
      const outputs = evaluate(provider, block, open);
      if (outputs === null) return;
      wanted.push(...outputs);
    }
    native.sync(dimension, location, wanted, limits().maxNativeCarriers);
    for (const provider of providers.filter(provider => provider.exclusive_quarters)) {
      const stack = wanted.filter(output => output.provider === provider.id);
      const contacts = Array(8).fill(0);
      for (const output of stack) {
        const effect = Object.values(provider.effects).find(effect => effect.entity === output.entity);
        for (let bit = 0; bit < 8; bit++) if (output.tile & (1 << bit)) contacts[bit] = Math.max(contacts[bit], effect.priority);
      }
      for (const output of stack) {
        const effect = Object.values(provider.effects).find(effect => effect.entity === output.entity);
        if (effect.entity_surface) output.quarters = Array.from({ length: 4 }, (_, q) => effect.mask_lookup[contacts[q] + 6 * contacts[(q + 1) % 4] + 36 * contacts[q + 4]]);
      }
    }
    const actors = wanted.filter(output => (!output.quarters || output.quarters.some(value => value > 0)) &&
      !Object.values(providers.find(provider => provider.id === output.provider).effects).some(effect => effect.entity === output.entity && effect.native_block && !effect.entity_surface));
    const keys = new Set(actors.map(output => descriptorKey(dimension.id, location, output)));
    for (const key of hosts.get(hostKey(dimension.id, location)) ?? []) {
      const record = owned.get(key);
      if (record && !keys.has(key)) remove(key, record);
    }
    for (const key of keys) {
      const record = owned.get(key);
      if (record && !record.entity.isValid) owned.delete(key);
    }
    if (actors.some(output => (failures.get(output.entity) ?? -1) > system.currentTick)) { removeHost(keys); return; }
    const missing = [...keys].filter(key => !owned.has(key)).length;
    if (owned.size + missing > limits().maxEntityCarriers) {
      capacitySkips++;
      // Admit a complete host stack; a partial one can reverse visible priority.
      removeHost(keys);
      return;
    }
    for (const output of actors) {
      const key = descriptorKey(dimension.id, location, output);
      let record = owned.get(key);
      try {
        if (!record) {
          const effect = effectOf(output), target = { x: location.x + .5, y: location.y + 1, z: location.z + .5 };
          let entity;
          if (effect.spawn_structure) {
            const nearby = { type: output.entity, location: target, maxDistance: .2 };
            const existing = new Set(dimension.getEntities(nearby).map(entity => entity.id));
            world.structureManager.place(effect.spawn_structure, dimension, { x: location.x, y: location.y + 1, z: location.z }, { includeEntities: true, includeBlocks: false });
            entity = dimension.getEntities(nearby).find(entity => !existing.has(entity.id) && !entity.hasTag(TAG));
            if (!entity) throw new Error('carrier template did not create an entity');
          } else entity = dimension.spawnEntity(output.entity, target);
          entity.addTag(TAG);
          const anchor = { dimension: dimension.id, ...location, type: block.typeId };
          entity.setDynamicProperty('bct:key', key);
          entity.setDynamicProperty('bct:anchor', JSON.stringify(anchor));
          entity.setDynamicProperty('bct:revision', REVISION);
          record = { entity, ...anchor };
          remember(key, record);
        }
        const effect = effectOf(output), values = { 'bct:face': FACES.indexOf(output.face), 'bct:tile': output.tile };
        output.quarters?.forEach((value, q) => { values['bct:quarter' + q] = value; });
        if (effect?.height_tuning && !effect.mask_lookup)
          values['bct:variation'] = effect.biome_tint === 'grass' ? ((Math.imul(location.x, 73428767) ^ Math.imul(location.z, 912931)) >>> 0) % 3 : 0;
        if (output.tint) {
          const palette = providers.find(provider => provider.id === output.provider)?.biome_palette;
          if (palette?.length) {
            let best = 0, distance = Infinity;
            palette.forEach((color, index) => {
              const delta = color.reduce((sum, value, channel) => sum + (value - output.tint[channel]) ** 2, 0);
              if (delta < distance) { best = index; distance = delta; }
            });
            values['bct:palette'] = best;
          }
          output.tint.forEach((value, index) => { values['bct:tint_' + ['r', 'g', 'b'][index]] = value; });
        }
        values['bct:active'] = true;
        writeProperties(record.entity, values);
        if (output.kind === 'model') record.entity.setRotation({ x: 0, y: [180, 270, 0, 90][FACES.indexOf(output.face)] });
        if (record.type !== block.typeId) {
          record.type = block.typeId;
          record.entity.setDynamicProperty('bct:anchor', JSON.stringify({ dimension: dimension.id, ...location, type: block.typeId }));
        }
        positionActor(record, settings.heights);
      } catch (error) {
        log(output.entity + ': ' + error);
        failures.set(output.entity, system.currentTick + 1200);
        removeHost(keys);
        return;
      }
    }
  }

  function clear() {
    pending.clear();
    recover();
    for (const [key, record] of owned) remove(key, record);
    native.clear();
  }

  /** Runs once per engine interval. */
  function maintain() {
    if (!providers.length) return;
    passes++;
    if (!recovered) {
      enabled = world.getDynamicProperty('bct:terrain_enabled') !== false;
      native.recover(); recover(); recovered = true;
      applyHeights(settings.heights);
    }
    native.flush();
    if (suspended) { if (passes % 10 === 0) native.clear(); return; }
    // Adopt newly loaded carriers and remove duplicates.
    if (passes % 10 === 0) recover();
    // Unloaded carriers stay saved with their chunk; only release script handles.
    for (const [key, record] of owned) if (!record.entity.isValid) owned.delete(key);
    if (enabled) validateAnchors();
  }

  /** Updates the eight hosts around an exposed source block. */
  function surface(dimension, location) {
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
      if (dx || dz) update(dimension, { x: location.x + dx, y: location.y, z: location.z + dz });
    }
  }

  /** A scan found a block of one of `blockTypes()`. */
  function found(dimension, location, typeId) {
    if (native.ids().has(typeId)) { update(dimension, { ...location, y: location.y - native.offset(typeId) }); return; }
    let above;
    try { above = dimension.getBlock({ ...location, y: location.y + 1 }); } catch { return; }
    if (above && (above.isAir || native.ids().has(above.typeId) || passable()?.has(above.typeId))) surface(dimension, location);
  }

  function despawn() {
    for (const [key, record] of [...owned]) if (!inReach(record.dimension, record)) remove(key, record);
  }

  function setProviders(next) {
    const incoming = validateProviders(next);
    const byId = new Map(incoming.map(provider => [provider.id, provider]));
    const changedIds = new Set(providers.filter(provider => JSON.stringify(provider) !== JSON.stringify(byId.get(provider.id))).map(provider => provider.id));
    const nativeTypes = new Set(providers.filter(provider => changedIds.has(provider.id)).flatMap(provider => Object.values(provider.effects).map(effect => effect.native_block).filter(Boolean)));
    for (const [key, record] of owned) {
      const owner = key.split('|')[2]?.split(':')[0];
      if (changedIds.has(owner)) remove(key, record);
    }
    native.recover();
    for (const entry of [...native.owners.values()]) if (nativeTypes.has(entry.type)) native.remove(entry);
    providers.splice(0, providers.length, ...incoming);
    native.idCache = null;
    native.staleGroups = null;
    for (const provider of incoming) for (const effect of Object.values(provider.effects)) failures.delete(effect.entity);
    native.flush();
    return providers;
  }

  function addProviders(supplied) {
    if (!Array.isArray(supplied)) throw new Error('Terrain sources must be an array');
    const merged = new Map(providers.map(provider => [provider.id, provider]));
    for (const provider of supplied) merged.set(provider.id, provider);
    return setProviders([...merged.values()]);
  }

  return {
    addProviders, setProviders, update, changed, found, surface, recover, maintain, despawn, clear,
    owned, providers, native, settings, applyHeights, validateAnchors,
    /** Block types the scanner looks for: transition sources, the replacements standing for them, and native carriers. */
    blockTypes: () => [...new Set([...providers.flatMap(provider => provider.scan_sources ?? provider.rules.flatMap(rule => rule.neighbors))
      .flatMap(type => [type, ...aliasesOf(type)]), ...native.ids()])],
    onEntityLoad(entity) {
      // The entity can be gone again before the event reaches scripts (a chunk loading and unloading at once).
      if (!providers.length || recoveryPending) return;
      let ours = false;
      try { ours = entity.isValid !== false && entity.hasTag(TAG); } catch { return; }
      if (ours) { recoveryPending = true; system.run(() => { recoveryPending = false; recover(); }); }
    },
    setEnabled(value) {
      enabled = value;
      world.setDynamicProperty('bct:terrain_enabled', value);
      if (!value) clear();
    },
    suspend() { if (suspended) return; suspended = true; clear(); },
    resume() { suspended = false; },
    trace: player => traceSurface(player, providers, (provider, block) => evaluate(provider, view.wrap(block), passable()), [...owned.values()]),
    get suspended() { return suspended; },
    get status() { return { enabled, actors: owned.size, native: native.size, providers: providers.length, capacitySkips }; },
  };
}
