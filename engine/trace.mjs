/** Bounded live evidence for corner selection and camera-relative placement. */
export function traceSurface(player, providers, evaluate, entityRecords = []) {
  const hit = player.getBlockFromViewDirection({maxDistance:12,includePassableBlocks:false,includeLiquidBlocks:false});
  if (!hit?.block) return null;
  const block = hit.block;
  const location = block.location;
  const nativeIds = new Set(providers.flatMap(provider => Object.values(provider.effects).map(effect => effect.native_block).filter(Boolean)));
  const nativeEffects = providers.flatMap(provider => Object.values(provider.effects)).filter(effect => effect.native_block);
  const entityEffects = nativeEffects.filter(effect => effect.entity_surface);
  const actorCells = new Map();
  for (const record of entityRecords) {
    if (record.dimension !== player.dimension.id) continue;
    try {
      if (!record.entity.isValid || !record.entity.getProperty('bct:active')) continue;
      if (!entityEffects.some(effect => effect.entity === record.entity.typeId)) continue;
      const key = [record.x,record.y,record.z].join(',');
      const actors = actorCells.get(key) ?? [];
      actors.push([record.entity.typeId,record.entity.getProperty('bct:tile'),record.entity.location]);
      actorCells.set(key,actors);
    } catch { /* An actor may retire between trace samples. */ }
  }
  const anchor = nativeIds.has(block.typeId) ? {...location,y:location.y-(nativeEffects.find(effect => effect.native_block === block.typeId)?.native_offset ?? 1)} : location;
  const types = [], rows = [], mismatches = [];
  for (let z=-3;z<=3;z++) {
    const row = [];
    for (let x=-3;x<=3;x++) {
      try {
        const host = player.dimension.getBlock({x:anchor.x+x,y:anchor.y,z:anchor.z+z});
        const above = player.dimension.getBlock({x:anchor.x+x,y:anchor.y+1,z:anchor.z+z});
        if (!host || !above) { row.push(null); continue; }
        for (const type of [host.typeId,above.typeId]) if (!types.includes(type)) types.push(type);
        const selected = providers.flatMap(provider => evaluate(provider,host) ?? []).map(output => [output.entity,output.tile]);
        let edges = above.permutation.getState('bct:edges') ?? 0;
        let corners = above.permutation.getState('bct:corners') ?? 0;
        for (let index=0;index<4;index++) {
          const packed = above.permutation.getState('bct:quadrant'+index);
          if (packed === undefined) continue;
          if (packed % 4 === 1) edges |= 1<<index;
          if (Math.floor(packed/4) === 1) corners |= 1<<index;
        }
        const carriers = [];
        for (const offset of [...new Set(nativeEffects.map(effect => effect.native_offset ?? 1))]) {
          const cell = offset === 1 ? above : player.dimension.getBlock({x:anchor.x+x,y:anchor.y+offset,z:anchor.z+z});
          if (!cell) { carriers.push([offset,null,null,[]]); continue; }
          if (!types.includes(cell.typeId)) types.push(cell.typeId);
          const packed = Array.from({length:4},(_,i) => cell.permutation.getState('bct:quadrant'+i) ?? null);
          const actual = nativeEffects.filter(effect => effect.native_block === cell.typeId && effect.native_material).map(effect => {
            let e=0,c=0;
            for(let i=0;i<4;i++) {
              if(packed[i] === null) continue;
              if(packed[i]%4 === effect.native_material)e|=1<<i;
              if(Math.floor(packed[i]/4) === effect.native_material)c|=1<<i;
            }
            return [effect.entity,e,c];
          });
          carriers.push([offset,types.indexOf(cell.typeId),packed,actual]);
        }
        const actors = actorCells.get([host.location.x,host.location.y,host.location.z].join(',')) ?? [];
        for (const [entity,mask] of selected) {
          const effect=nativeEffects.find(effect => effect.entity===entity);
          if(!effect)continue;
          if(effect.entity_surface) {
            const matches=actors.filter(actor=>actor[0]===entity);
            const actualMask=matches.reduce((mask,actor)=>mask | actor[1],0);
            if(mask!==actualMask || matches.length!==1)mismatches.push({host:host.location,hostType:host.typeId,entity,renderer:'entity',selected:mask,actual:actualMask,actors:matches.length});
            continue;
          }
          const carrier=carriers.find(item=>item[0]===(effect.native_offset ?? 1));
          const actual=carrier?.[3].find(item=>item[0]===entity);
          const actualMask=actual ? actual[1]+16*actual[2] : 0;
          if(mask!==actualMask)mismatches.push({host:{x:anchor.x+x,y:anchor.y,z:anchor.z+z},hostType:host.typeId,entity,renderer:'native_block',selected:mask,actual:actualMask,offset:effect.native_offset ?? 1,disabled:!!effect.native_disabled});
        }
        for(const actor of actors) {
          if(!selected.some(output=>output[0]===actor[0]))mismatches.push({host:host.location,hostType:host.typeId,entity:actor[0],renderer:'entity',selected:0,actual:actor[1],unexpected:true});
        }
        row.push([types.indexOf(host.typeId),types.indexOf(above.typeId),
          edges,corners,selected,carriers,actors]);
      } catch { row.push(null); }
    }
    rows.push(row);
  }
  return {anchor,dimension:player.dimension.id,player:player.location,rotation:player.getRotation(),
    hitFace:hit.face,faceLocation:hit.faceLocation,types,mismatches,
    layout:'rows north to south, columns west to east; cells host,above,grassEdges,grassCorners,selected,carriers,actors; carriers offset,type,quadrants,actualMaterials; actors entity,mask,location',rows};
}
