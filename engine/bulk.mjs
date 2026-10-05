/**
 * Bulk block writes: many blocks set to the same permutation in one native call.
 *
 * Setting blocks one by one from script costs a call into the game per block,
 * and a forest chunk holds a couple of thousand leaves; that is what made trees,
 * walls and edges change seconds after they loaded. Dimension.fillBlocks over a
 * ListBlockVolume sets any list of positions to one permutation natively, so a
 * whole chunk changes in a few dozen calls (one per distinct look). Writes are
 * collected per dimension and permutation, then `flush` sends them.
 *
 * fillBlocks replaces the block in place; like setPermutation it does not keep
 * a waterlogged block's water, so callers write waterlogged blocks one by one
 * (and set the water again) instead of adding them here.
 */

/** api: @minecraft/server (BlockPermutation, ListBlockVolume). log(message) reports a refused write. */
export function createBulkWriter({ api, log = () => {} }) {
  const groups = new Map();   // dimension id + permutation key -> { dimension, permutation, locations }
  const permutations = new Map();   // permutation key -> resolved permutation
  let pending = 0;

  /** The permutation for a block type and states, resolved once per writer. */
  function permutationOf(type, states) {
    const key = type + JSON.stringify(states, Object.keys(states).sort());
    let permutation = permutations.get(key);
    if (!permutation) { permutation = api.BlockPermutation.resolve(type, states); permutations.set(key, permutation); }
    return { key, permutation };
  }

  return {
    get pending() { return pending; },
    /** Queues one block: type and states as BlockPermutation.resolve takes them. */
    set(dimension, location, type, states = {}) {
      const { key, permutation } = permutationOf(type, states);
      const name = dimension.id + '|' + key;
      let group = groups.get(name);
      if (!group) { group = { dimension, permutation, locations: [] }; groups.set(name, group); }
      group.locations.push({ x: location.x, y: location.y, z: location.z });
      pending++;
    },
    /** Sends every queued write; returns the locations written, as [{ dimension, location, permutation }]. */
    flush() {
      const written = [];
      for (const { dimension, permutation, locations } of groups.values()) {
        if (api.ListBlockVolume && dimension.fillBlocks) {
          try {
            dimension.fillBlocks(new api.ListBlockVolume(locations), permutation, { ignoreChunkBoundErrors: true });
            for (const location of locations) written.push({ dimension, location, permutation });
            continue;
          } catch (error) { log('bulk write ' + String(error)); }
        }
        // Without fillBlocks (or when it refused), block by block; only the blocks set are reported.
        for (const location of locations) {
          try {
            const block = dimension.getBlock(location);
            if (!block) continue;
            block.setPermutation(permutation);
            written.push({ dimension, location, permutation });
          } catch (error) { log('block write ' + String(error)); }
        }
      }
      groups.clear();
      pending = 0;
      return written;
    },
  };
}
