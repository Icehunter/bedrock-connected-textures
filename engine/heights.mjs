export const DEFAULT_HEIGHTS = Object.freeze([1, 2, 3, 4, 5]);
export const HEIGHT_MATERIALS = Object.freeze([
  Object.freeze({key: 'soul_sand', label: 'Soul sand'}),
  Object.freeze({key: 'red_sand', label: 'Red sand'}),
  Object.freeze({key: 'suspicious_sand', label: 'Suspicious sand'}),
  Object.freeze({key: 'sand', label: 'Sand'}),
  Object.freeze({key: 'grass', label: 'Grass'}),
]);

function parseHeights(values) {
  const parsed = values.map((value, index) => {
    if (typeof value !== 'string' || !/^[+]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(value.trim())) {
      throw new Error(HEIGHT_MATERIALS[index].label + ' needs a decimal height.');
    }
    const height = Number(value);
    if (!Number.isFinite(height) || height < .25 || height > 16) {
      throw new Error(HEIGHT_MATERIALS[index].label + ' must be between 0.25 and 16 px.');
    }
    return height;
  });
  for (let index = 1; index < parsed.length; index++) {
    if (parsed[index] <= parsed[index - 1]) {
      throw new Error(HEIGHT_MATERIALS[index].label + ' must be higher than ' + HEIGHT_MATERIALS[index - 1].label.toLowerCase() + '.');
    }
  }
  return Object.freeze(parsed);
}

export function createHeightSettings({ModalFormData, applyHeights, report = () => {}}) {
  let heights = DEFAULT_HEIGHTS, revision = 0;
  const pending = new Set();

  function notify(player, message) {
    try { player?.sendMessage(message); } catch { /* The player may have left while the form was open. */ }
    try { report('height test: ' + message); } catch { /* Reporting does not change the active heights. */ }
  }

  function apply(nextHeights, player) {
    try { applyHeights(nextHeights); }
    catch (error) {
      notify(player, 'Heights could not be applied: ' + String(error));
      return false;
    }
    heights = nextHeights;
    revision++;
    const active = HEIGHT_MATERIALS.map((material, index) => material.label.toLowerCase() + ' ' + heights[index]).join(', ');
    notify(player, 'Height test: ' + active + ' px above terrain. Resets on reload.');
    return true;
  }

  return {
    get heights() { return heights; },
    reset(player) { return apply(DEFAULT_HEIGHTS, player); },
    async open(player) {
      const playerKey = player?.id ?? player;
      if (pending.has(playerKey)) {
        notify(player, 'Height settings are already open.');
        return false;
      }
      pending.add(playerKey);
      const openedRevision = revision;
      try {
        const form = new ModalFormData().title('Terrain edge heights');
        HEIGHT_MATERIALS.forEach((material, index) => {
          form.textField(material.label + ' height (texture px)', '0.25 to 16', {defaultValue: String(heights[index])});
        });
        const response = await form
          .toggle('Reset to 1 / 2 / 3 / 4 / 5 px', {defaultValue: false})
          .label('Absolute heights above terrain, lowest to highest. Temporary for this world session; resets on reload.')
          .submitButton('Apply')
          .show(player);
        if (response?.canceled) return false;
        if (revision !== openedRevision) {
          notify(player, 'Heights changed while this form was open. Open it again.');
          return false;
        }
        const values = response?.formValues;
        if (response?.canceled !== false || !Array.isArray(values) || values.length < 6 ||
            values.length > 7 || values.slice(6).some(value => value !== undefined) || typeof values[5] !== 'boolean') {
          notify(player, 'Invalid height form. Settings unchanged.');
          return false;
        }
        let parsed;
        try { parsed = parseHeights(values.slice(0, 5)); }
        catch (error) {
          notify(player, error.message + ' Settings unchanged.');
          return false;
        }
        return apply(values[5] ? DEFAULT_HEIGHTS : parsed, player);
      } catch (error) {
        notify(player, 'Height settings could not be opened: ' + String(error));
        return false;
      } finally {
        pending.delete(playerKey);
      }
    },
  };
}
