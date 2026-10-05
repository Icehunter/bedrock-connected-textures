import fs from 'node:fs';
import assert from 'node:assert/strict';
import { createTerrain } from '../engine/terrain.mjs';
function fixture(entries, unknown = []) {
  const blocks = new Map(entries.map(([location, type]) => [location.join(','), type]));
  const unloaded = new Set(unknown.map(location => location.join(',')));
  const tints = new Map(), states = new Map(), writes = [], biomes = new Map();
  const dimension = {getBiome: location => ({id:biomes.get([location.x,location.y,location.z].join(',')) ?? 'minecraft:plains'})};
  const get = location => ({
    dimension,
    typeId: blocks.get(location.join(',')) ?? 'minecraft:air',
    get isAir() { return this.typeId === 'minecraft:air'; },
    get permutation() {
      const type=blocks.get(location.join(',')), snapshot={...states.get(location.join(','))};
      return {type,states:snapshot,
        getState: name => snapshot[name],
        getAllStates: () => ({...snapshot}),
        withState: (name,value) => ({type,states:{...snapshot,[name]:value}}),
      };
    },
    setType(type) { blocks.set(location.join(','),type);states.delete(location.join(',')); },
    setPermutation(value) { writes.push(value);blocks.set(location.join(','),value.type);states.set(location.join(','),value.states); },
    location: { x: location[0], y: location[1], z: location[2] },
    getComponent(name) {
      assert.equal(name, 'minecraft:map_color');
      const tint = tints.get(location.join(',')) ?? [.6,.78,.55];
      const color = {red: .4, green: .8, blue: .2, alpha: 1};
      return {color, tintedColor: {red: .4*tint[0], green: .8*tint[1], blue: .2*tint[2], alpha: 1}};
    },
    offset({ x, y, z }) {
      const next = location.map((value, axis) => value + [x, y, z][axis]);
      if (unloaded.has(next.join(','))) throw new Error('Chunk unavailable');
      return get(next);
    },
  });
  return { get, blocks, tints, states, writes, biomes, dimension };
}

export function runtime(suppliedProvider, saved) {
  const suppliedProviders = Array.isArray(suppliedProvider) ? suppliedProvider : [suppliedProvider];
  const grid = saved?.grid ?? fixture([[[0, 0, 0], 'minecraft:oak_log'], [[0, -1, 0], 'minecraft:grass_block']]);
  const entities = saved?.entities ?? []; let nextId = entities.length;
  const dimension = {
    id: 'minecraft:overworld',
    getBiome: location => grid.dimension.getBiome(location),
    getBlock: ({ x, y, z }) => grid.get([x, y, z]),
    getEntities: ({ tags = [], type }) => entities.filter(entity => entity.isValid && (!type || entity.typeId === type) && tags.every(tag => entity.tags.has(tag))),
    spawnEntity(typeId, location) {
      const entity = { id: 'e' + nextId++, typeId, location, isValid: true, tags: new Set(),
        properties: new Map(['r','g','b'].map(channel=>['bct:tint_'+channel,1])), dynamic: new Map(), teleports: [],
        addTag(tag) { this.tags.add(tag); },
        hasTag(tag) { return this.tags.has(tag); },
        setProperty(key, value) { this.properties.set(key, value); },
        getProperty(key) { return this.properties.get(key); },
        setDynamicProperty(key, value) { this.dynamic.set(key, value); },
        getDynamicProperty(key) { return this.dynamic.get(key); },
        setRotation(rotation) { this.rotation = rotation; },
        teleport(location) { this.location = {...location}; this.teleports.push({...location}); },
        remove() { this.isValid = false; },
      };
      entities.push(entity); return entity;
    },
  };
  const properties = saved?.properties ?? new Map();
  const players = [], scheduled = [], delayed = [];
  const forms = [], formResponses = [];
  class ModalFormData {
    constructor() { this.fields = []; forms.push(this); }
    title(value) { this.heading = value; return this; }
    dropdown(...args) { this.fields.push(['dropdown',...args]); return this; }
    textField(...args) { this.fields.push(['textField',...args]); return this; }
    toggle(...args) { this.fields.push(['toggle',...args]); return this; }
    label(...args) { this.fields.push(['label',...args]); return this; }
    submitButton(value) { this.submit = value; return this; }
    async show(player) { this.player = player; return formResponses.shift() ?? {canceled:true}; }
  }
  let host;
  const world = { structureManager:{place(name,_dimension,position) { const effect=host.providers.flatMap(provider=>Object.values(provider.effects)).find(effect=>effect.spawn_structure===name); dimension.spawnEntity(effect.entity,{x:position.x+.5,y:position.y,z:position.z+.5}); }},
    getDimension: () => dimension, getAllPlayers: () => players,
    setDynamicProperty: (key, value) => properties.set(key, value),
    getDynamicProperty: key => properties.get(key) };
  const system = { currentTick: 0, run: callback => scheduled.push(callback), runTimeout: (callback,ticks) => delayed.push({callback,ticks}) };
  const limits = { maxEntityCarriers: 1500, maxNativeCarriers: 8000 };
  host = createTerrain({ api: { world, system, ModalFormData, BlockPermutation: { resolve: (type, states) => ({ type, states }) } },
    limits: () => limits, providers: structuredClone(suppliedProviders) });
  return { host, ...host, limits, dimension, grid, entities, properties, system, players, scheduled, delayed, forms, formResponses };
}
