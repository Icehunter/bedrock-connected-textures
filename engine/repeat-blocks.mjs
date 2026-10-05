/**
 * Repeat-pattern blocks keep their place in the pattern as bct:x/y/z states.
 * The pattern size comes from each block's component parameters.
 */
export function installRepeatBlocks(system) {
  const mod=(value,period)=>((value%period)+period)%period;
  const update=({block},{params}={})=>{
    if(!params||!Number.isInteger(params.height)||!Number.isInteger(params.period)||params.height<1||params.period<1)return;
    let permutation=block.permutation,changed=false;
    for(const axis of ['x','y','z']) {
      const name='bct:'+axis,value=mod(block.location[axis],axis==='y'?params.height:params.period);
      if(permutation.getState(name)!==value){permutation=permutation.withState(name,value);changed=true;}
    }
    if(changed)block.setPermutation(permutation);
  };
  system.beforeEvents.startup.subscribe(event=>event.blockComponentRegistry.registerCustomComponent(
    'bct:update_repeat',{onPlace:update,onTick:update}));
}
