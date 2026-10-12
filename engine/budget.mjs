/**
 * One time budget for all of the engine's work in a game tick, shared by every
 * part: chunk scans, replacement swaps, leaf conversion, terrain edges,
 * connected-texture carriers and overlay cells.
 *
 * Bedrock warns when a pack's scripts take more than a few milliseconds a tick
 * on average, or one tick runs long. Parts therefore work in small steps and
 * stop once the time they were given is up. `share` hands every part with work
 * an equal slice of what is left of `limit()` milliseconds this tick, then the
 * time the others did not need, never more than the part's own cap. A part
 * can overrun its slice by one step, so the part that goes first changes every
 * tick: otherwise the last part (overlay cells) would get nothing while the
 * others always have work. A part whose `first()` is true (work right next to
 * players) runs before the others, up to its cap and within half the tick. Work done outside the parts
 * (polls, schedules) is counted with `measure`.
 */
export function createBudget({ system, now, limit }) {
  let tick = -1, spent = 0, turn = 0, since = system.currentTick, outside = 0;
  const totals = new Map();   // part name -> milliseconds since the last stats()
  const add = (part, ms) => { if (part.name) totals.set(part.name, (totals.get(part.name) ?? 0) + ms); };
  const roll = () => { if (system.currentTick !== tick) { tick = system.currentTick; spent = 0; } };
  return {
    /** Average milliseconds a tick per part (and outside the parts) since the last call, then starts again. */
    stats() {
      const ticks = Math.max(1, system.currentTick - since), result = { ticks, outside: +(outside / ticks).toFixed(2) };
      for (const [name, ms] of totals) result[name] = +(ms / ticks).toFixed(2);
      totals.clear(); outside = 0; since = system.currentTick;
      return result;
    },
    /** Milliseconds of this tick's budget not used yet. */
    left() { roll(); return limit() - spent; },
    /** Runs work that is not a part and counts its time against this tick. */
    measure(fn, name) {
      roll();
      const start = now();
      try { return fn(); } finally {
        const ms = now() - start; spent += ms; outside += ms;
        if (name) totals.set('outside.' + name, (totals.get('outside.' + name) ?? 0) + ms);
      }
    },
    /**
     * Gives this tick's remaining time to the parts: each `{ run(until), cap?() }`
     * works until now() reaches `until` and returns true while it has more to do.
     */
    share(parts) {
      roll();
      const start = now(), total = limit() - spent, used = new Map();
      // The parts that go first share at most half the tick, so the others always get time.
      const firstEnd = start + total / 2;
      for (const part of parts) {
        if (!part.first?.()) continue;
        const begin = now();
        const room = Math.min(part.cap?.() ?? Infinity, firstEnd - begin);
        if (room <= 0) break;
        part.run(begin + room);
        used.set(part, now() - begin);
        add(part, now() - begin);
      }
      const first = parts.length ? turn++ % parts.length : 0;
      let active = [...parts.slice(first), ...parts.slice(0, first)];
      for (let round = 0; active.length && round < 16; round++) {
        const left = total - (now() - start);
        if (left <= 0) break;
        const slice = left / active.length, next = [];
        for (const part of active) {
          const begin = now();
          const room = Math.min(slice, total - (begin - start), (part.cap?.() ?? Infinity) - (used.get(part) ?? 0));
          if (room <= 0) continue;
          const more = part.run(begin + room);
          used.set(part, (used.get(part) ?? 0) + now() - begin);
          add(part, now() - begin);
          if (more) next.push(part);
        }
        active = next;
      }
      spent += now() - start;
    },
  };
}
