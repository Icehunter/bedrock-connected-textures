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
// A host a scan reached from one source block is not updated again from the next for this many ticks.
const SURFACE_TICKS = 40;
// Hosts remembered before the old ones are dropped.
const SURFACED_LIMIT = 20000;

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
  const owned = new Map(), hosts = new Map(), failures = new Map(), pending = new Map(), surfaced = new Map();
  const hostKey = (dimension, location) => dimension + ':' + location.x + ':' + location.y + ':' + location.z;
  const knownTypes = new Map();
  /** Whether the game knows a block type (remembered: the answer never changes in a session). */
  const known = type => {
    if (!knownTypes.has(type)) knownTypes.set(type, !api.BlockTypes?.get || Boolean(api.BlockTypes.get(type)));
    return knownTypes.get(type);
  };
  const settings = createHeightSettings({ ModalFormData: api.ModalFormData, applyHeights, report: log });
  const native = new NativeSurfaces(world, (type, states) => api.BlockPermutation.resolve(type, states), () => providers,
    () => limits().maxNativeCarriers, () => system.currentTick, log, undefined, true);
  let enabled = true, recovered = false, suspended = false, recoveryPending = false, flushing = false;
  let passes = 0, capacitySkips = 0, validationCursor = 0, prunedAt = -1;

  function remember(key, record) {
    owned.set(key, record);
    const id = hostKey(record.dimension, record);
    if (!hosts.has(id)) hosts.set(id, new Set());
    hosts.get(id).add(key);
  }

  // Providers and their effects by id and carrier entity, built once per provider change: update() runs for every host.
  let lookups = null;
  const lookup = () => lookups ??= {
    provider: new Map(providers.map(provider => [provider.id, provider])),
    effect: new Map(providers.map(provider => {
      const byEntity = new Map();
      for (const effect of Object.values(provider.effects)) if (!byEntity.has(effect.entity)) byEntity.set(effect.entity, effect);
      return [provider.id, byEntity];
    })),
    // Entities a provider draws with native blocks rather than carriers.
    nativeEntities: new Map(providers.map(provider => [provider.id, new Set(Object.values(provider.effects)
      .filter(effect => effect.native_block && !effect.entity_surface).map(effect => effect.entity))])),
    exclusive: providers.filter(provider => provider.exclusive_quarters),
  };
  const effectOf = output => lookup().effect.get(output.provider)?.get(output.entity);

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
    for (const provider of lookup().exclusive) {
      const effects = lookup().effect.get(provider.id);
      const stack = wanted.filter(output => output.provider === provider.id);
      const contacts = Array(8).fill(0);
      for (const output of stack) {
        const effect = effects.get(output.entity);
        for (let bit = 0; bit < 8; bit++) if (output.tile & (1 << bit)) contacts[bit] = Math.max(contacts[bit], effect.priority);
      }
      for (const output of stack) {
        const effect = effects.get(output.entity);
        if (effect.entity_surface) output.quarters = Array.from({ length: 4 }, (_, q) => effect.mask_lookup[contacts[q] + 6 * contacts[(q + 1) % 4] + 36 * contacts[q + 4]]);
      }
    }
    const actors = wanted.filter(output => (!output.quarters || output.quarters.some(value => value > 0)) &&
      !lookup().nativeEntities.get(output.provider).has(output.entity));
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
          const palette = lookup().provider.get(output.provider)?.biome_palette;
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

  /**
   * Updates the eight hosts around an exposed source block a scan found. A host next to several
   * sources is updated once per SURFACE_TICKS, and a neighbour of the source's own type is skipped:
   * a block's edges never reach a block of its own type. Block events still update hosts at once.
   */
  function surface(dimension, location, typeId) {
    const tick = system.currentTick;
    // Old hosts are dropped at most once a tick; a Map keeps insertion order, so the oldest come first.
    if (surfaced.size > SURFACED_LIMIT && prunedAt !== tick) {
      prunedAt = tick;
      for (const [key, at] of surfaced) { if (tick - at < SURFACE_TICKS) break; surfaced.delete(key); }
    }
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
      if (!dx && !dz) continue;
      const host = { x: location.x + dx, y: location.y, z: location.z + dz }, key = hostKey(dimension.id, host);
      if (tick - (surfaced.get(key) ?? -Infinity) < SURFACE_TICKS) continue;
      if (typeId) {
        let neighbor;
        try { neighbor = dimension.getBlock(host); } catch { continue; }
        if (neighbor?.typeId === typeId) continue;
      }
      // Re-inserted, so the Map stays ordered oldest first.
      surfaced.delete(key); surfaced.set(key, tick);
      update(dimension, host);
    }
  }

  /**
   * Scanner consumer: the hosts of one chunk between low and high that have a source next to them.
   * Visiting every source would visit every block of a grass field, so instead each group of rules
   * with the same targets and sources reads both as position lists (short type lists, one call each),
   * walks the shorter list and looks for the other in the 26 blocks around it. Hosts under existing
   * native surfaces are updated too, so an edge whose source has gone is removed.
   */
  function* scanChunk(dimension, record, low, high) {
    if (!enabled || suspended || !providers.length) return;
    // A type the game does not know (a Java name such as minecraft:bricks) makes a block filter throw.
    const withAliases = types => [...new Set(types.flatMap(type => [type, ...aliasesOf(type)]))].filter(known);
    const groups = new Map();
    for (const provider of providers) for (const rule of provider.rules) {
      const key = [...rule.targets].sort().join() + '|' + [...rule.neighbors].sort().join();
      if (!groups.has(key)) {
        const targets = withAliases(rule.targets), sources = withAliases(rule.neighbors);
        // An empty list would match every block.
        if (targets.length && sources.length) groups.set(key, { targets, sources, targetSet: new Set(targets), sourceSet: new Set(sources) });
      }
    }
    const natives = [...native.ids()].filter(known), x0 = record.x * 16, z0 = record.z * 16;
    // Sections follow the game's 16-block sections; the band's ends cut the first and last.
    for (let bottom = low, top; bottom <= high; bottom = top + 1) {
      top = Math.min(high, Math.floor(bottom / 16) * 16 + 15);
      const section = new api.BlockVolume({ x: x0, y: bottom, z: z0 }, { x: x0 + 15, y: top, z: z0 + 15 });
      const around = new api.BlockVolume({ x: x0 - 1, y: bottom - 1, z: z0 - 1 }, { x: x0 + 16, y: top + 1, z: z0 + 16 });
      const pending = new Map();   // hosts to update, by key
      if (natives.length && dimension.containsBlock(section, { includeTypes: natives }, true))
        for (const at of dimension.getBlocks(section, { includeTypes: natives }, true).getBlockLocationIterator()) {
          const typeId = dimension.getBlock(at)?.typeId;
          if (native.ids().has(typeId)) { const host = { x: at.x, y: at.y - native.offset(typeId), z: at.z }; pending.set(hostKey(dimension.id, host), host); }
        }
      yield;
      for (const group of groups.values()) {
        if (!dimension.containsBlock(section, { includeTypes: group.targets }, true) ||
          !dimension.containsBlock(around, { includeTypes: group.sources }, true)) continue;
        const targets = dimension.getBlocks(section, { includeTypes: group.targets }, true);
        const sources = dimension.getBlocks(around, { includeTypes: group.sources }, true);
        yield;
        // The shorter list is walked; the longer one is only asked about the blocks around each entry.
        const walkSources = sources.getCapacity() <= targets.getCapacity();
        const [walked, other] = walkSources ? [sources, targets] : [targets, sources];
        let reads = 0;
        for (const at of walked.getBlockLocationIterator()) {
          around: for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (let dz = -1; dz <= 1; dz++) {
            if (!dx && !dy && !dz) continue;
            const near = { x: at.x + dx, y: at.y + dy, z: at.z + dz };
            if (!other.isInside(near)) continue;
            const host = walkSources ? near : { x: at.x, y: at.y, z: at.z };
            pending.set(hostKey(dimension.id, host), host);
            // A walked host needs only one source next to it.
            if (!walkSources) break around;
          }
          if (++reads % 16 === 0) yield;
        }
      }
      let updates = 0;
      for (const host of pending.values()) { update(dimension, host); if (++updates % 4 === 0) yield; }
      yield;
    }
    yield* topsOutsideBand(dimension, x0, z0, low, high, [...groups.values()]);
  }

  /**
   * Terrain above or below the band (a mountain seen from a valley): the topmost block of each column of
   * the chunk and the ring around it, paired by type like the band's sections. One native call a column.
   */
  function* topsOutsideBand(dimension, x0, z0, low, high, groups) {
    const wanted = new Set(groups.flatMap(group => [...group.targets, ...group.sources]));
    const tops = new Map();   // 'x,z' -> { x, y, z, typeId }
    let reads = 0;
    for (let x = x0 - 1; x <= x0 + 16; x++) for (let z = z0 - 1; z <= z0 + 16; z++) {
      let top;
      try { top = dimension.getTopmostBlock({ x, z }); } catch { continue; }
      if (top && (top.location.y < low || top.location.y > high) && wanted.has(top.typeId))
        tops.set(x + ',' + z, { x, y: top.location.y, z, typeId: top.typeId });
      if (++reads % 16 === 0) yield;
    }
    for (const host of tops.values()) {
      if (host.x < x0 || host.x > x0 + 15 || host.z < z0 || host.z > z0 + 15) continue;
      const paired = groups.some(group => group.targetSet.has(host.typeId) && [-1, 0, 1].some(dx => [-1, 0, 1].some(dz => {
        const near = (dx || dz) && tops.get((host.x + dx) + ',' + (host.z + dz));
        return near && Math.abs(near.y - host.y) <= 1 && group.sourceSet.has(near.typeId);
      })));
      if (paired) { update(dimension, { x: host.x, y: host.y, z: host.z }); yield; }
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
    lookups = null;
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
    addProviders, setProviders, update, changed, found, surface, scan: scanChunk, recover, maintain, despawn, clear,
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
    /** The chunks holding generated edge blocks, and removing the ones whose chunks are loaded now. */
    nativeChunks: () => native.ownedChunks(),
    nativeTypes: () => [...native.ids()],
    removeNative: () => native.clear(),
  };
}
