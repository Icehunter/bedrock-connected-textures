/**
 * A chunk scanner shared by engine parts. Each consumer declares a chunk
 * radius, a vertical band around the player and the block types it wants; a
 * slab of a chunk is asked once for the union of the types whose consumers
 * reach that chunk. Consumers with `surface` also get a top-surface pass
 * through getTopmostBlock; a consumer with `prepare` gets a context per slab,
 * passed to `found` and then to `finish`; a consumer with `scan` reads its
 * chunks itself.
 *
 * Chunks are scanned in the order `rank` gives (by default the nearest first,
 * in the engine the ones players head for first), and again when marked dirty
 * or when a player moves more than half the band up or down. Scans are
 * generators of small steps advanced by `run(until)` within the engine's
 * shared time budget: block queries go by 16-layer slabs, a slab without a
 * wanted block costs one containsBlock call, and consumers only queue work for
 * their own parts.
 */
const PROBES_PER_PLAYER = 96;
const SLAB = 16;
// A chunk next to a player is scanned again this many milliseconds after its scans: a chunk can
// report loaded a moment before its blocks can be read.
const RECHECKS_MS = [1500, 6000];

function spiral(radius) {
  const offsets = [];
  for (let x = -radius; x <= radius; x++) for (let z = -radius; z <= radius; z++) offsets.push({ x, z });
  return offsets.sort((a, b) => Math.max(Math.abs(a.x), Math.abs(a.z)) - Math.max(Math.abs(b.x), Math.abs(b.z)) || a.x * a.x + a.z * a.z - b.x * b.x - b.z * b.z);
}

export class Scanner {
  /**
   * consumers: [{radius(), band(), types(), found(dimension, location, typeId, context), surface?(dimension, location),
   * prepare?(dimension, record, low, high), finish?(context), scan?(dimension, record, low, high) (a generator)}]
   * jobs(): how many chunk scans may run at once. rank(record): the order chunks are scanned in (lower first).
   */
  constructor({ system, BlockVolume, consumers, jobs = () => 2, now = () => Date.now(), log = () => {}, rank = record => record.distance,
    profile = () => false }) {
    this.system = system; this.BlockVolume = BlockVolume; this.consumers = consumers; this.jobs = jobs; this.log = log; this.profile = profile;
    this.now = now; this.rank = rank;
    this.chunks = new Map(); this.cursors = new Map(); this.active = new Map(); this.offsets = []; this.radius = -1;
    // Native calls the scans made since the last backlog(): name -> { calls, ms }.
    this.calls = new Map(); this.scanned = 0;
  }

  /** The dimension as scans see it: its block queries timed, for backlog(). */
  timed(dimension) {
    const calls = this.calls, now = this.now;
    return new Proxy(dimension, {
      get(target, name) {
        const value = target[name];
        if (typeof value !== 'function') return value;
        return (...args) => {
          const start = now();
          try { return value.apply(target, args); } finally {
            const entry = calls.get(name) ?? { calls: 0, ms: 0 };
            entry.calls++; entry.ms += now() - start; calls.set(name, entry);
          }
        };
      },
    });
  }

  get radiusLimit() { return Math.max(0, ...this.consumers.map(consumer => consumer.radius())); }

  /** Whether scans are running. */
  get busy() { return this.active.size > 0; }

  /** Marks every known chunk for a rescan. */
  reset() { for (const record of this.chunks.values()) record.dirty = true; }

  /** Cancels running scans and forgets every chunk. */
  stop() { this.active.clear(); this.chunks.clear(); this.cursors.clear(); }

  markDirty(dimensionId, location) {
    const record = this.chunks.get(dimensionId + ':' + Math.floor(location.x / 16) + ':' + Math.floor(location.z / 16));
    if (record) record.dirty = true;
  }

  /** Finds the chunks around players and starts the scans most worth doing, up to jobs(). */
  poll(players) {
    const radius = this.radiusLimit;
    if (radius !== this.radius) { this.radius = radius; this.offsets = spiral(radius); }
    const ids = new Set(players.map(player => player.id));
    for (const id of this.cursors.keys()) if (!ids.has(id)) this.cursors.delete(id);
    for (const player of players) {
      const cx = Math.floor(player.location.x / 16), cz = Math.floor(player.location.z / 16);
      const signature = player.dimension.id + ':' + cx + ':' + cz;
      let cursor = this.cursors.get(player.id);
      if (cursor?.signature !== signature) { cursor = { signature, index: 0 }; this.cursors.set(player.id, cursor); }
      for (let work = 0; work < Math.min(PROBES_PER_PLAYER, this.offsets.length); work++) {
        const offset = this.offsets[cursor.index++ % this.offsets.length];
        this.probe(player.dimension, cx + offset.x, cz + offset.z, player.location.y);
      }
    }
    const band = Math.max(4, ...this.consumers.map(consumer => consumer.band()));
    for (const [key, record] of this.chunks) {
      const nearest = this.nearest(players, record);
      if (!nearest) { if (!this.active.has(key)) this.chunks.delete(key); continue; }
      record.distance = nearest.distance;
      // A player who climbs or digs past half the band needs the new layers.
      if (Math.abs(nearest.player.location.y - record.y) > band / 2) { record.y = Math.floor(nearest.player.location.y); record.dirty = true; }
    }
    const time = this.now();
    for (const record of this.chunks.values())
      if (record.recheckAt !== undefined && time >= record.recheckAt) { record.recheckAt = undefined; record.dirty = true; }
    this.start();
  }

  /** Starts the dirty chunks most worth scanning, up to jobs(). */
  start() {
    if (this.active.size >= this.jobs()) return;
    const pending = [];
    for (const record of this.chunks.values()) if (record.dirty && record.loaded && !this.active.has(record.key)) { record.rank = this.rank(record); pending.push(record); }
    // Only the few best are needed: picked one at a time rather than sorting every pending chunk.
    while (this.active.size < this.jobs() && pending.length) {
      let best = 0;
      for (let index = 1; index < pending.length; index++) if (pending[index].rank < pending[best].rank) best = index;
      const [record] = pending.splice(best, 1);
      if (!this.loaded(record.dimension, record.x, record.z, record.y)) { record.loaded = false; continue; }
      record.dirty = false;
      // Native calls are timed for status only while debugging: the wrapper costs a little on every call.
      this.active.set(record.key, this.scan(record, this.profile() ? this.timed(record.dimension) : record.dimension));
    }
  }

  /** Chunks known, waiting for a scan, being scanned, and the nearest waiting one's distance. */
  backlog() {
    let dirty = 0, nearest = Infinity;
    for (const record of this.chunks.values()) if (record.dirty && record.loaded) { dirty++; nearest = Math.min(nearest, record.distance); }
    const calls = Object.fromEntries([...this.calls].map(([name, entry]) => [name, [entry.calls, +entry.ms.toFixed(1)]]));
    const result = { chunks: this.chunks.size, dirty, active: this.active.size, nearestDirty: dirty ? nearest : null, scanned: this.scanned, calls };
    this.calls.clear(); this.scanned = 0;
    return result;
  }

  /** Whether a chunk within `distance` chunks of a player is being scanned or waits for a scan. */
  nearWork(distance) {
    for (const record of this.chunks.values())
      if (record.distance <= distance && record.loaded && (record.dirty || this.active.has(record.key))) return true;
    return false;
  }

  /** After a scan: a chunk next to a player is looked at again a few times. */
  finished(key) {
    this.scanned++;
    const record = this.chunks.get(key);
    if (!record) return;
    if (record.distance > 1) { record.rechecks = 0; return; }
    const count = record.rechecks ?? 0;
    if (count < RECHECKS_MS.length) { record.recheckAt = this.now() + RECHECKS_MS[count]; record.rechecks = count + 1; }
  }

  /** Advances the running scans in turn until `until` (a now() time). Returns true while scans remain. */
  run(until) {
    while (this.active.size && this.now() < until) {
      for (const [key, scan] of this.active) {
        let result;
        try { result = scan.next(); } catch (error) { result = { done: true }; this.log('chunk scan ' + key + ': ' + String(error)); }
        if (result.done) {
          this.active.delete(key);
          this.finished(key);
          // The next chunk starts at once rather than at the next poll.
          this.start();
        }
        if (this.now() >= until) break;
      }
    }
    return this.active.size > 0;
  }

  /** The closest player in range of a chunk, or undefined once every player has left. */
  nearest(players, record) {
    let best;
    for (const player of players) {
      if (player.dimension.id !== record.dimension.id) continue;
      const distance = Math.max(Math.abs(record.x - Math.floor(player.location.x / 16)), Math.abs(record.z - Math.floor(player.location.z / 16)));
      if (distance <= this.radius + 1 && (!best || distance < best.distance)) best = { player, distance };
    }
    return best;
  }

  loaded(dimension, x, z, y) {
    try { return dimension.isChunkLoaded({ x: x * 16, y, z: z * 16 }); } catch { return false; }
  }

  probe(dimension, x, z, y) {
    const key = dimension.id + ':' + x + ':' + z;
    let record = this.chunks.get(key);
    if (record?.loaded) return;
    const loaded = this.loaded(dimension, x, z, y);
    if (!loaded) { if (record) record.loaded = false; return; }
    if (!record) {
      // Seen for the first time: its neighbours' scans read it if it was loaded then, so they stay as they are.
      record = { key, dimension, x, z, y: Math.floor(y), loaded: true, dirty: true, distance: 0, rank: 0 };
      this.chunks.set(key, record);
      return;
    }
    record.loaded = true; record.dirty = true;
    // A chunk that loads again can complete a connection across its border.
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {
      const neighbor = this.chunks.get(dimension.id + ':' + (x + dx) + ':' + (z + dz));
      if (neighbor && neighbor !== record) neighbor.dirty = true;
    }
  }

  *scan(record, dimension = record.dimension) {
    try {
      const range = dimension.heightRange;
      const reached = this.consumers.filter(consumer => record.distance <= consumer.radius());
      if (!reached.length) return;
      for (const consumer of reached) {
        if (!consumer.surface) continue;
        const types = new Set(consumer.types());
        if (!types.size) continue;
        for (let z = record.z * 16; z < record.z * 16 + 16; z++) {
          if (!this.loaded(dimension, record.x, record.z, record.y)) { record.dirty = true; return; }
          for (let x = record.x * 16; x < record.x * 16 + 16; x++) {
            const top = dimension.getTopmostBlock({ x, z });
            if (top && types.has(top.typeId)) { consumer.surface(dimension, top.location, top.typeId); yield; }
            else if (x % 4 === 3) yield;
          }
        }
      }
      const generic = [];
      for (const consumer of reached) {
        const band = consumer.band(), low = Math.max(range.min, record.y - band), high = Math.min(range.max - 1, record.y + band);
        if (low > high) continue;
        if (consumer.scan) yield* consumer.scan(dimension, record, low, high);
        else generic.push(consumer);
      }
      if (!generic.length) return;
      const wanted = new Map();
      for (const consumer of generic) for (const type of consumer.types()) {
        if (!wanted.has(type)) wanted.set(type, []);
        wanted.get(type).push(consumer);
      }
      if (!wanted.size) return;
      const types = [...wanted.keys()], band = Math.max(...generic.map(consumer => consumer.band()));
      const low = Math.max(range.min, record.y - band), high = Math.min(range.max - 1, record.y + band);
      for (let bottom = low, top; bottom <= high; bottom = top + 1) {
        // Slabs follow the game's 16-block sections.
        top = Math.min(high, Math.floor(bottom / SLAB) * SLAB + SLAB - 1);
        const volume = new this.BlockVolume({ x: record.x * 16, y: bottom, z: record.z * 16 }, { x: record.x * 16 + 15, y: top, z: record.z * 16 + 15 });
        if (!dimension.containsBlock(volume, { includeTypes: types }, true)) { yield; continue; }
        // A consumer's context per slab; a generator prepare takes its own steps.
        const contexts = new Map();
        for (const consumer of generic) {
          if (!consumer.prepare) continue;
          let context = consumer.prepare(dimension, record, bottom, top);
          if (typeof context?.next === 'function') context = yield* context;
          contexts.set(consumer, context);
          yield;
        }
        const list = dimension.getBlocks(volume, { includeTypes: types }, true);
        yield;
        let reads = 0;
        for (const location of list.getBlockLocationIterator()) {
          const typeId = dimension.getBlock(location)?.typeId;
          let used = false;
          for (const consumer of wanted.get(typeId) ?? [])
            if (Math.abs(location.y - record.y) <= consumer.band()) { consumer.found(dimension, location, typeId, contexts.get(consumer)); used = true; }
          // A found block can cost a consumer real work (terrain hosts, carriers): one per step.
          if (used || ++reads % 8 === 0) yield;
        }
        for (const [consumer, context] of contexts) consumer.finish?.(context);
      }
    } catch (error) {
      record.dirty = true;
      this.log('chunk scan ' + record.key + ': ' + String(error));
    }
  }
}
