/** Connected textures: entity carriers drawn over block faces, one instance per converted pack. */
import { ruleResolver, carrierPosition, chooseTile, javaModelIndex } from './tiles.mjs';
import { FACE_EDGES, DIRECTIONS, vanillaView } from './core.mjs';
import { writeProperties } from './settings.mjs';

const FACES = Object.keys(FACE_EDGES);
const TAG = 'bct_connected';
const DIMENSIONS = ['overworld', 'nether', 'the_end'];

/**
 * Carriers for one converted pack's rules. `ownsBlock` decides which block
 * types this pack draws when several packs list the same block; `carriers` is
 * the shared allowance; `inReach` says whether a location is inside the
 * active scan area. A replacement block reads as the vanilla block it stands
 * for (`vanillaType`, `vanillaStates`) wherever a rule looks at neighbors, and
 * gets no carriers: its replacement already draws the author's look.
 * `aliasesOf` gives the replacement types the scanner also looks for, so
 * carriers left on a block that was swapped are removed.
 */
export function createConnectedSource(configuration, { api, providerId = 'local', ownsBlock = () => true, carriers, inReach = () => true, log = () => {},
  vanillaType = typeId => typeId, aliasesOf = () => [], vanillaStates = undefined }) {
const { world, system } = api;
const view = vanillaView(vanillaType, vanillaStates);
const rules = configuration.rules ?? [];
let disposed = false, enabled = true;
const defer = handler => system.run(() => { if (!disposed) handler(); });
const leafDistances=new Map(),leafTypes=new Set(configuration.leafDistanceLeaves??[]),logTypes=new Set(configuration.leafDistanceLogs??[]);
function leafDistance(block) {
  const id=`${block.dimension.id}:${block.location.x}:${block.location.y}:${block.location.z}`;
  const cached=leafDistances.get(id),tick=system.currentTick??0;
  if(cached&&tick-cached.tick<20)return cached.value;
  const queue=[{block,distance:0}],seen=new Set([id]);let unknown=8,result=7;
  for(let index=0;index<queue.length;index++) {
    const entry=queue[index];if(entry.distance>=6)continue;
    for(const direction of Object.values(DIRECTIONS)) {
      let neighbor;
      try {neighbor=entry.block.offset(Object.fromEntries(['x','y','z'].map((axis,i)=>[axis,direction[i]])));} catch {}
      const distance=entry.distance+1;
      if(!neighbor){unknown=Math.min(unknown,distance);continue;}
      if(logTypes.has(neighbor.typeId)){result=distance;index=queue.length;break;}
      if(!leafTypes.has(neighbor.typeId))continue;
      const key=`${neighbor.dimension.id}:${neighbor.location.x}:${neighbor.location.y}:${neighbor.location.z}`;
      if(!seen.has(key)){seen.add(key);queue.push({block:neighbor,distance});}
    }
  }
  const value=unknown<result?undefined:result;
  if(leafDistances.size>16384)leafDistances.clear();
  leafDistances.set(id,{tick,value});return value;
}
const stateValue=(block,name)=>{
  if(name==='bct:leaf_distance')return leafDistance(block);
  if(name==='bct:vine_up') {
    let above;
    try {above=block.offset({x:0,y:1,z:0});} catch {return undefined;}
    if(!above)return undefined;
    const type=above.typeId;
    if(['minecraft:air','minecraft:cave_air','minecraft:void_air','minecraft:water','minecraft:flowing_water',
      'minecraft:lava','minecraft:flowing_lava','minecraft:vine'].includes(type))return false;
    const ordinaryCube=['minecraft:stone','minecraft:granite','minecraft:diorite','minecraft:andesite',
      'minecraft:polished_granite','minecraft:polished_diorite','minecraft:polished_andesite',
      'minecraft:cobblestone','minecraft:mossy_cobblestone','minecraft:stone_bricks','minecraft:stonebrick',
      'minecraft:bricks','minecraft:brick_block','minecraft:dirt','minecraft:coarse_dirt','minecraft:rooted_dirt',
      'minecraft:grass_block','minecraft:grass','minecraft:mycelium','minecraft:podzol'];
    if(ordinaryCube.includes(type)||(type.startsWith('minecraft:')&&/_planks$|_log$|_wood$/.test(type))||leafTypes.has(type)||
      ['minecraft:farmland','minecraft:grass_path','minecraft:dirt_path'].includes(type))return true;
    if(type.startsWith('minecraft:')&&type.includes('double_')&&type.endsWith('_slab'))return true;
    if(type.startsWith('minecraft:')&&type.endsWith('_slab')) {
      const half=above.permutation.getState('minecraft:vertical_half');
      return half==='bottom'?true:half==='top'?false:undefined;
    }
    if(type.startsWith('minecraft:')&&type.endsWith('_stairs')) {
      const upsideDown=above.permutation.getState('upside_down_bit');
      return upsideDown===false?true:upsideDown===true?false:undefined;
    }
    return undefined;
  }
  if(name==='bct:snowy') {
    const above=block.offset({x:0,y:1,z:0});
    return above?['minecraft:snow','minecraft:snow_layer'].includes(above.typeId):undefined;
  }
  return block.permutation.getState(name);
};
const stateMatches=(states,block)=>Object.entries(states??{}).every(([name,value])=>
  (Array.isArray(value)?value:[value]).map(String).includes(String(stateValue(block,name))));
const variantOf=block=>{
  const variant=(configuration.baseTextureVariants?.[block.typeId]??[]).find(entry=>stateMatches(entry.states,block));
  if(!variant?.modelChoices)return variant;
  if(!['java-26.2-block-position','java-26.2-multipart-block-position'].includes(variant.modelSelection))return undefined;
  return {...variant,...variant.modelChoices[javaModelIndex(block.location,variant.modelChoices.map(choice=>choice.weight),variant.modelSelection)]};
};
const modelFace=(block,face,layer=-1)=>{
  const variant=variantOf(block);
  if(layer>=0)return variant?.faceLayers?.[face]?.[layer];
  return {texture:variant?.faces?.[face]??configuration.baseTextures?.[block.typeId]?.[face]??block.typeId,
    orientation:variant?.orientations?.[face]??configuration.textureOrientations?.[block.typeId]?.[face],
    tintIndex:variant?.tintIndices?.[face]??-1};
};
const fullCubes=new Set(configuration.fullCubeBlocks??[]),opaque=new Set(configuration.opaqueBlocks??[]),partBlocks=new Set(configuration.modelPartBlocks??[]);
const customTintBlocks=new Set(configuration.customTintBlocks??[]);
const hasOrientationProvider=Object.keys(configuration.textureOrientations??{}).length>0 || Object.keys(configuration.baseTextureVariants??{}).length>0;
const providers={dialect:configuration.dialect??'optifine',stateOf:stateValue,
  // Rules limited to biomes read the block's biome (undefined while its chunk is not readable).
  biomeOf:block=>{try{return block.dimension.getBiome(block.location)?.id;}catch{return undefined;}},
  logicalBlockOf:block=>variantOf(block)?.javaBlock??configuration.nativeBlockJavaIds?.[block.typeId]??block.typeId,
  ...(hasOrientationProvider?{orientationOf:(block,face)=>variantOf(block)?.orientations?.[face]??configuration.textureOrientations?.[block.typeId]?.[face]}:{}),
  tintOf:(block,tintBlock,index)=>{
    const logical=variantOf(block)?.javaBlock??configuration.nativeBlockJavaIds?.[block.typeId]??block.typeId;
    const custom=customTintBlocks.has(tintBlock)||(tintBlock===logical&&customTintBlocks.has(block.typeId));
    if(index<0&&!custom)return [1,1,1];
    const type=configuration.modelTintTypes?.[tintBlock]??
      (tintBlock===logical?configuration.modelTintTypes?.[block.typeId]:undefined)??
      (['minecraft:grass_block','minecraft:grass'].includes(tintBlock)?'grass':undefined);
    if(Array.isArray(type))return type;
    if(!type)return [1,1,1];
    const biome=block.dimension.getBiome(block.location)?.id;
    return (type==='foliage'?configuration.foliageTints:type==='grass'?configuration.grassTints:configuration.customTints?.[type])?.[biome];
  },
  ...(fullCubes.size?{fullCube:block=>fullCubes.has(block.typeId),opaque:block=>opaque.has(block.typeId)}:{})};
const rulesById=new Map(rules.map(rule=>[rule.id,rule])),modelRenderBlocks=new Set(configuration.modelRenderBlocks??[]);
const selectedModelChoices=(block,variant=variantOf(block))=>[variant,...(variant?.modelPartsGroups??[]).map(group=>
  group.modelChoices[javaModelIndex(block.location,group.modelChoices.map(choice=>choice.weight),group.modelSelection)])].filter(Boolean);
function partNeighborTexture(block,face,preferred) {
  const textures=selectedModelChoices(block).flatMap(choice=>(choice.modelParts??[]).flatMap(part=>
    Object.entries(part.faces??{}).filter(([sourceFace,data])=>(data.worldFace??sourceFace)===face).map(([,data])=>data.texture)));
  if(!partBlocks.has(block.typeId)||fullCubes.has(block.typeId))textures.push(modelFace(block,face)?.texture);
  return textures.includes(preferred)?preferred:textures[0];
}
const paletteMaps=new WeakMap();
const tintKey=tint=>tint.map(value=>Math.round(value*255)).join(',');
function paletteSelection(selected,tint=selected.tint??[1,1,1]) {
  const palette=selected.rule.tintPalette;
  if(!palette)return {...selected,tint,palette:0};
  if(!paletteMaps.has(palette))paletteMaps.set(palette,new Map(palette.map((color,index)=>[tintKey(color),index])));
  const index=paletteMaps.get(palette).get(tintKey(tint));
  return index===undefined?undefined:{...selected,tint,palette:index};
}
const contexts=new Map();
function modelContext(layer) {
  if(!contexts.has(layer)) {
    const textureOf=(block,face)=>modelFace(block,face,layer)?.texture;
    const contextProviders=layer<0?providers:{...providers,orientationOf:(block,face)=>modelFace(block,face,layer)?.orientation};
    contexts.set(layer,{textureOf,providers:contextProviders,resolve:ruleResolver(rules.filter(rule=>!rule.fallback),contextProviders)});
  }
  return contexts.get(layer);
}
function resolveFace(block,face) {
  if(!ownsBlock(block.typeId))return {known:true,visible:false,layers:[],modelLayers:[],ownership:false};
  const variant=variantOf(block);
  if(partBlocks.has(block.typeId)&&configuration.baseTextureVariants?.[block.typeId]?.length&&!variant)return {known:false,reason:'model_state_provider',layers:[],modelLayers:[]};
  const count=variantOf(block)?.faceLayers?.[face]?.length??0,layers=[],overlays=[],selections=[];
  for(let index=-1;index<count&&(!fullCubes.size||fullCubes.has(block.typeId));index++) {
    const material=modelFace(block,face,index),context=modelContext(index);
    let selected=context.resolve(block,face,context.textureOf);
    if(!selected.known)return {...selected,layers:[],modelLayers:selections};
    if(!selected.visible) {
      const fallbackId=index<0?((modelRenderBlocks.has(block.typeId)?configuration.modelTextureRules?.[material.texture]:undefined)??
        configuration.fallbackFaces?.[block.typeId]?.[face]):
        configuration.modelTextureRules?.[material.texture];
      const fallback=rulesById.get(fallbackId);
      if((index>=0||modelRenderBlocks.has(block.typeId))&&!fallback)
        return {known:false,reason:'model_texture_fallback',layers:[],modelLayers:selections};
      if(fallback)selected={...chooseTile(fallback,block,face,context.textureOf,material.texture,context.providers),
        rule:fallback,texture:material.texture,overlays:selected.overlays??[]};
    }
    if(!selected.known)return {...selected,layers:[],modelLayers:selections};
    if(selected.visible) {
      const tint=providers.tintOf(block,providers.logicalBlockOf(block),material.tintIndex??-1);
      if(!tint)return {known:false,reason:'model_tint_provider',layers:[],modelLayers:selections};
      const colored=paletteSelection(selected,tint);
      if(!colored)return {known:false,reason:'model_tint_palette',layers:[],modelLayers:selections};
      layers.push({...colored,layer:index+1});
    }
    overlays.push(...(selected.overlays??[]));selections.push(selected);
  }
  for(let index=0;index<overlays.length;index++) {
    const colored=paletteSelection(overlays[index]);
    if(!colored)return {known:false,reason:'overlay_tint_palette',layers:[],modelLayers:selections};
    layers.push({...colored,layer:count+1+index});
  }
  const choices=selectedModelChoices(block,variant);
  let slot=1000;
  let cullMask=0;
  const cullFaces=new Set(choices.flatMap(choice=>(choice.modelRenderGroups??[]).flatMap(material=>material.cullFaces??[])));
  for(const [index,direction] of Object.values(DIRECTIONS).entries()) {
    if(!cullFaces.has(Object.keys(DIRECTIONS)[index]))continue;
    let neighbor;
    try {neighbor=block.offset(Object.fromEntries(['x','y','z'].map((axis,i)=>[axis,direction[i]])));} catch {}
    if(!neighbor)return {known:false,reason:'model_group_neighbor',layers:[],modelLayers:selections};
    if(neighbor&&(neighbor.typeId===block.typeId||opaque.has(neighbor.typeId)))cullMask|=1<<index;
  }
  for(const choice of choices)for(const material of choice.modelRenderGroups??[]) {
    const layer=slot++;if(material.worldFace!==face)continue;
    const rule=rulesById.get(material.rule),tint=providers.tintOf(block,providers.logicalBlockOf(block),material.tintIndex??-1);
    if(!rule||!tint)return {known:false,reason:'model_group_material',layers:[],modelLayers:selections};
    const colored=paletteSelection({known:true,visible:true,tile:0,orientation:0,rule,texture:material.texture},tint);
    if(!colored)return {known:false,reason:'model_group_tint_palette',layers:[],modelLayers:selections};
    layers.push({...colored,layer,positionLayer:0,modelPart:true,cullMask});
  }
  for(const choice of choices)for(const part of choice.modelParts??[])for(const [sourceFace,material] of Object.entries(part.faces??{})) {
    if(material.bundled)continue;
    const layer=slot++;if((material.worldFace??sourceFace)!==face)continue;
    const textureOf=(candidate,target)=>candidate===block?material.texture:partNeighborTexture(candidate,target,material.texture);
    const contextProviders={...providers,canonicalOutput:true,orientationOf:()=>material.orientation??0,
      faceOccludes:(candidate,neighbor)=>!!material.cullface&&neighbor!=null&&opaque.has(neighbor.typeId)};
    const resolve=ruleResolver(rules.filter(rule=>!rule.fallback),contextProviders);
    let selected=resolve(block,face,textureOf);
    if(!selected.known)return {...selected,layers:[],modelLayers:selections};
    if(!selected.visible) {
      const fallback=rulesById.get(material.fallbackRule);
      if(!fallback)return {known:false,reason:'model_part_fallback',layers:[],modelLayers:selections};
      selected={...chooseTile(fallback,block,face,textureOf,material.texture,contextProviders),rule:fallback,texture:material.texture};
    }
    if(!selected.visible)continue;
    const tint=providers.tintOf(block,providers.logicalBlockOf(block),material.tintIndex??-1);
    if(!tint)return {known:false,reason:'model_part_tint',layers:[],modelLayers:selections};
    const renderer=rulesById.get(material.renderers?.[selected.rule.id]);
    if(!renderer)return {known:false,reason:'model_part_renderer',layers:[],modelLayers:selections};
    const colored=paletteSelection({...selected,rule:renderer},tint);
    if(!colored)return {known:false,reason:'model_part_tint_palette',layers:[],modelLayers:selections};
    layers.push({...colored,layer,positionLayer:0,modelPart:true});
  }
  return {...selections[0],known:true,layers,modelLayers:selections.slice(1)};
}

const layeredBlocks=Object.entries(configuration.baseTextureVariants??{}).filter(([,entries])=>entries.some(entry=>
  (entry.modelChoices??[entry]).some(choice=>Object.values(choice.faceLayers??{}).some(layers=>layers.length)))).map(([block])=>block);
function knownType(id) {
  try {if(api.BlockTypes.get(id))return true;} catch {}
  log('unknown block type '+id);return false;
}
const sources=[...new Set([...rules.flatMap(rule=>rule.blocks),...(configuration.sourceBlocks??[]),...modelRenderBlocks,...layeredBlocks,...partBlocks])]
  .filter(id=>(!fullCubes.size||fullCubes.has(id)||partBlocks.has(id))&&knownType(id));
const carrierTypes=new Set(rules.map(rule=>rule.entity).filter(Boolean));
const records=new Map(),faceLayers=new Map(),recoveryQueue=new Set();
let validationCursor=0;

const key=(dimension,p)=>`${dimension.id}:${p.x}:${p.y}:${p.z}`;
const carrierTarget=(location,face,layer)=>{
  const target=carrierPosition(location,face);
  for(let axis=0;axis<3;axis++)target[['x','y','z'][axis]]+=DIRECTIONS[face][axis]*layer/1024;
  return target;
};
function storeAnchor(entity,anchor) {
  entity.setDynamicProperty('bct:anchor',JSON.stringify(anchor));
  entity.setDynamicProperty('bct:source',providerId);
}
function removeRecord(id) {
  const record=records.get(id);
  if(!record)return;
  records.delete(id);
  carriers.release(record.dimension,record.location);
  try {if(record.entity.isValid)record.entity.remove();} catch {}
}
function spawnCarrier(dimension,location,rule) {
  const above={x:location.x+.5,y:location.y+1,z:location.z+.5};
  // Passive cloud carriers need the structure's duration and radius; a plain
  // spawn would let them expire.
  if(!rule.spawn_structure)return dimension.spawnEntity(rule.entity,above);
  const nearby={type:rule.entity,location:above,maxDistance:.2};
  const existing=new Set(dimension.getEntities(nearby).map(actor=>actor.id));
  world.structureManager.place(rule.spawn_structure,dimension,{x:location.x,y:location.y+1,z:location.z},{includeEntities:true,includeBlocks:false});
  return dimension.getEntities(nearby).find(actor=>!existing.has(actor.id)&&!actor.hasTag(TAG));
}
function update(dimension,location) {
  if(disposed||!enabled) return;
  let block;
  try {block=view.wrap(dimension.getBlock(location));} catch {return;}
  if(!block) return;
  for(const face of FACES) {
    const prefix=key(dimension,location)+':'+face+':';
    let selected;
    try {selected=block.replaced?{known:true,visible:false,layers:[]}:resolveFace(block,face);} catch(error) {log('selection '+String(error));continue;}
    if(!selected.known)continue;
    const wanted=new Set(selected.layers.map(entry=>prefix+entry.layer));
    for(const id of faceLayers.get(prefix)??[])if(!wanted.has(id))removeRecord(id);
    if(wanted.size)faceLayers.set(prefix,wanted);else faceLayers.delete(prefix);
    for(const layer of selected.layers) {
      const id=prefix+layer.layer,rule=layer.rule;
      try {
        let record=records.get(id);
        if(record&&(!record.entity.isValid||record.entity.typeId!==rule.entity)){removeRecord(id);record=undefined;}
        if(!record) {
          if(!carriers.claim(dimension.id,location))continue;
          let entity;
          try {entity=spawnCarrier(dimension,location,rule);} catch(error) {carriers.release(dimension.id,location);throw error;}
          if(!entity){carriers.release(dimension.id,location);throw new Error('carrier template did not create an entity');}
          entity.addTag(TAG);
          storeAnchor(entity,{location,face,layer:layer.layer});
          entity.setRotation({x:0,y:0});
          record={entity,dimension:dimension.id,location:{...location}};
          records.set(id,record);
        }
        const entity=record.entity;
        const target=carrierTarget(location,face,layer.positionLayer??layer.layer);
        if(['x','y','z'].some(axis=>Math.abs(entity.location[axis]-target[axis])>1e-6))entity.teleport(target);
        const tint=rule.tintPalette?[1,1,1]:layer.tint??[1,1,1];
        writeProperties(entity,{'bct:face':FACES.indexOf(face),'bct:tile':layer.tile,'bct:palette':layer.palette??0,
          'bct:orientation':layer.orientation??0,...(rule.modelGroup?{'bct:cull_mask':layer.cullMask??0}:{}),
          'bct:tint_r':tint[0],'bct:tint_g':tint[1],'bct:tint_b':tint[2],'bct:active':true});
      } catch(error) {log('carrier '+id+' '+String(error));}
    }
  }
}
function recover(entity) {
  if(disposed||!entity.isValid||!carrierTypes.has(entity.typeId)||!entity.hasTag(TAG))return;
  const owner=entity.getDynamicProperty('bct:source');
  if(owner!==undefined&&owner!==providerId)return;
  if(!enabled){entity.remove();return;}
  try {
    const anchor=JSON.parse(entity.getDynamicProperty('bct:anchor')??'null');
    if(!anchor?.location||!['x','y','z'].every(axis=>Number.isInteger(anchor.location[axis]))||
      !FACES.includes(anchor.face)||!Number.isInteger(anchor.layer??0)||(anchor.layer??0)<0){entity.remove();return;}
    const location=anchor.location,face=anchor.face,layer=anchor.layer??0;
    if(!inReach(entity.dimension.id,location)){entity.remove();return;}
    const block=view.wrap(entity.dimension.getBlock(location));
    if(!block)return;
    if(block.replaced||!sources.includes(block.typeId)||!ownsBlock(block.typeId)){entity.remove();return;}
    const selected=resolveFace(block,face);
    if(!selected.known)return;
    if(!selected.layers.some(entry=>entry.layer===layer&&entity.typeId===entry.rule.entity)){entity.remove();return;}
    const id=key(entity.dimension,location)+':'+face+':'+layer;
    const existing=records.get(id);
    if(existing?.entity.isValid) {
      if(existing.entity.id!==entity.id)defer(()=>{if(entity.isValid)entity.remove();});
      return;
    }
    if(existing)removeRecord(id);
    if(!carriers.claim(entity.dimension.id,location)){entity.remove();return;}
    records.set(id,{entity,dimension:entity.dimension.id,location});
    const prefix=key(entity.dimension,location)+':'+face+':';
    if(!faceLayers.has(prefix))faceLayers.set(prefix,new Set());
    faceLayers.get(prefix).add(id);
    const blockKey=key(entity.dimension,location);
    if(!recoveryQueue.has(blockKey)) {
      recoveryQueue.add(blockKey);
      defer(()=>{recoveryQueue.delete(blockKey);update(entity.dimension,location);});
    }
  } catch(error) {entity.remove();log('recovery '+String(error));}
}
/** Removes every carrier of this pack, tracked or not yet adopted. */
function clear() {
  for(const id of [...records.keys()])removeRecord(id);
  faceLayers.clear();recoveryQueue.clear();
  for(const name of DIMENSIONS) {
    let entities=[];
    try {entities=world.getDimension(name).getEntities({tags:[TAG]});} catch {}
    for(const entity of entities) {
      const owner=entity.getDynamicProperty('bct:source');
      if(owner===undefined||owner===providerId)try {entity.remove();} catch {}
    }
  }
}
/** Drops handles of unloaded carriers and refreshes a few anchors per call. */
function maintain() {
  for(const [id,record] of records)if(!record.entity.isValid){records.delete(id);carriers.release(record.dimension,record.location);}
  const entries=[...records.values()];
  for(let work=0;work<Math.min(4,entries.length);work++) {
    const record=entries[validationCursor++ % entries.length];
    update(record.entity.dimension,record.location);
  }
}
/** Removes carriers whose block left the active area. */
function despawn() {
  for(const [id,record] of [...records]) {
    if(inReach(record.dimension,record.location))continue;
    removeRecord(id);
    const prefix=id.slice(0,id.lastIndexOf(':')+1);
    faceLayers.get(prefix)?.delete(id);
    if(!faceLayers.get(prefix)?.size)faceLayers.delete(prefix);
  }
}
function probe(raw) {
  const block=view.wrap(raw);
  return Object.fromEntries(FACES.map(face=>{
    const selected=resolveFace(block,face);
    return [face,{...selected,rule:selected.rule?.id,layers:selected.layers?.map(entry=>({...entry,rule:entry.rule?.id})),
      modelLayers:selected.modelLayers?.map(entry=>({...entry,rule:entry.rule?.id}))}];
  }));
}
function refreshOwnership() {
  if(disposed)return;
  const visited=new Set();
  for(const record of [...records.values()]) {
    const id=record.dimension+':'+record.location.x+':'+record.location.y+':'+record.location.z;
    if(!visited.has(id)){visited.add(id);update(record.entity.dimension,record.location);}
  }
}
function dispose() {
  if(disposed)return;
  clear();disposed=true;enabled=false;
}
defer(()=>{for(const name of DIMENSIONS)for(const entity of world.getDimension(name).getEntities({tags:[TAG]}))recover(entity);});
return {providerId,dispose,clear,maintain,despawn,refreshOwnership,invalidate(){leafDistances.clear();},resolveFace,variantOf,paletteSelection,probe,update,recover,
  blockTypes:()=>sources.filter(ownsBlock).flatMap(type=>[type,...aliasesOf(type)]),sources,
  setEnabled(value){enabled=value;},
  get status(){return {enabled:enabled&&!disposed,disposed,actors:records.size};}};
}

/** Every converted pack's connected-texture data, drawn under one carrier allowance. */
export function createConnected({ api, carriers, inReach, log, vanillaType, aliasesOf, vanillaStates }) {
  const sources = new Map(), instances = new Map(), owners = new Map();
  function setSource(provider, data) {
    if (!Array.isArray(data?.rules)) throw new Error('Invalid connected texture data');
    sources.set(provider, data);
    owners.clear();
    const order = [...sources].sort((a, b) => (a[1].sourcePriority ?? 0) - (b[1].sourcePriority ?? 0) || a[0].localeCompare(b[0]));
    for (const [id, config] of order) for (const block of new Set([...config.rules.flatMap(rule => rule.blocks ?? []),
      ...(config.sourceBlocks ?? []), ...(config.modelRenderBlocks ?? []), ...(config.modelPartBlocks ?? [])])) owners.set(block, id);
    instances.get(provider)?.dispose();
    instances.set(provider, createConnectedSource(data, { api, providerId: provider, ownsBlock: block => owners.get(block) === provider, carriers, inReach, log,
      vanillaType, aliasesOf, vanillaStates }));
    for (const instance of instances.values()) instance.refreshOwnership();
  }
  const each = method => (...args) => { for (const instance of instances.values()) instance[method](...args); };
  return {
    setSource, instances,
    blockTypes: () => [...new Set([...instances.values()].flatMap(instance => instance.blockTypes()))],
    update: each('update'), recover: each('recover'), invalidate: each('invalidate'), maintain: each('maintain'), despawn: each('despawn'), clear: each('clear'),
    setEnabled: each('setEnabled'),
    probe: block => Object.fromEntries([...instances].map(([id, instance]) => [id, instance.probe(block)])),
    get status() { return { sources: instances.size, actors: [...instances.values()].reduce((sum, instance) => sum + instance.status.actors, 0) }; },
  };
}
