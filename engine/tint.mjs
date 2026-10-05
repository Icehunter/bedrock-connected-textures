/** Recover the engine-evaluated biome multiplier without a biome color table. */
export function grassTint(block, colors) {
  if (colors && block.dimension) {
    try {
      const biome = block.dimension.getBiome(block.location);
      return colors[biome.id] ?? null;
    } catch { return null; }
  }
  try {
    const component = block.getComponent('minecraft:map_color');
    if (!component) return null;
    const base = component.color, tinted = component.tintedColor;
    const result = ['red', 'green', 'blue'].map(channel => {
      if (!Number.isFinite(base[channel]) || base[channel] <= 0 || !Number.isFinite(tinted[channel])) return NaN;
      return Math.max(0, Math.min(1, tinted[channel] / base[channel]));
    });
    return result.every(Number.isFinite) ? result : null;
  } catch { return null; }
}
