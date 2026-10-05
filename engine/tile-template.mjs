/**
 * Tile numbers of the standard 47-tile OptiFine connected-texture template,
 * keyed by the canonical neighbor mask from core.mjs (bits 0-3: up, right,
 * down, left edges in texture space; bits 4-7: the corner after each edge,
 * counted only when both of its edges connect). Checked against
 * tests/fixtures/tile-neighborhoods.json.
 */
export const TILE_TEMPLATE = Object.freeze({"0":0,"1":36,"2":1,"3":16,"4":12,"5":24,"6":4,"7":6,"8":3,"9":17,"10":2,"11":18,"12":5,"13":19,"14":7,"15":46,"19":37,"23":30,"27":40,"31":8,"38":13,"39":28,"46":31,"47":9,"55":25,"63":23,"76":15,"77":43,"78":29,"79":21,"95":34,"110":14,"111":22,"127":45,"137":39,"139":42,"141":41,"143":20,"155":38,"159":11,"175":35,"191":33,"205":27,"207":10,"223":32,"239":44,"255":26});
