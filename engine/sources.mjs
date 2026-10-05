/**
 * Receives the data converted packs publish (publisher.mjs) over script
 * events. Each packet names its part of the engine (`connected`, `terrain` or
 * `replace`), its pack and a checksum; data arrives in chunks of up to 750
 * characters.
 */
export const SOURCE_PARTS = Object.freeze(['connected', 'terrain', 'replace']);

export function checksum(text) {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i++) hash = Math.imul(hash ^ text.charCodeAt(i), 16777619) >>> 0;
  return hash.toString(16).padStart(8, '0');
}

export function listenSources({ system, onSource, log = () => {} }) {
  const pending = new Map(), loaded = new Map();
  const nonce = Math.random().toString(36).slice(2);
  let stopped = false;
  const ack = packet => system.sendScriptEvent('bct:source_ack', JSON.stringify({ engine: packet.engine, provider: packet.provider, digest: packet.digest, nonce }));
  const receive = event => {
    if (stopped || event.id !== 'bct:source_data' || event.sourceEntity) return;
    let packet;
    try { packet = JSON.parse(event.message); } catch { return; }
    const { engine, provider, digest, index, count, payload } = packet;
    if (!SOURCE_PARTS.includes(engine) || typeof provider !== 'string' || !/^[a-z0-9_-]{1,80}$/.test(provider) ||
      typeof digest !== 'string' || !/^[0-9a-f]{8}$/.test(digest) ||
      !Number.isInteger(index) || !Number.isInteger(count) || count < 1 || count > 16000 || index < 0 || index >= count ||
      typeof payload !== 'string' || payload.length > 750) return;
    const id = engine + '/' + provider;
    if (loaded.get(id) === digest) { ack(packet); return; }
    if (!pending.has(id) && pending.size >= 32) return;
    let transfer = pending.get(id);
    if (!transfer || transfer.digest !== digest || transfer.count !== count) {
      transfer = { digest, count, parts: new Array(count), received: 0 };
      pending.set(id, transfer);
    }
    if (transfer.parts[index] === undefined) { transfer.parts[index] = payload; transfer.received++; }
    else if (transfer.parts[index] !== payload) { pending.delete(id); return; }
    if (transfer.received !== count) return;
    const text = transfer.parts.join('');
    pending.delete(id);
    if (text.length > 8 * 1024 * 1024 || checksum(text) !== digest) return;
    try {
      onSource(engine, provider, JSON.parse(text), digest);
      loaded.set(id, digest);
      ack(packet);
    } catch (error) { log('source ' + id + ' rejected: ' + String(error)); }
  };
  system.afterEvents.scriptEventReceive.subscribe(receive);
  const request = () => { if (!stopped) system.sendScriptEvent('bct:source_request', JSON.stringify({ nonce })); };
  const first = system.run(request), interval = system.runInterval(request, 100);
  return {
    loaded,
    dispose() {
      stopped = true;
      system.clearRun(first); system.clearRun(interval);
      system.afterEvents.scriptEventReceive.unsubscribe(receive);
      pending.clear();
    },
  };
}
