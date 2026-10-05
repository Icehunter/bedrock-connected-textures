"""Java block states as Bedrock block states, through a checked reference table.

data/java-bedrock-states.json lists every Java state combination of the blocks
both editions share, next to the Bedrock block and states that show it. A Java
state selector (a blockstate variant, a CTM state filter) becomes the fewest
Bedrock state conditions that pick exactly the same rows. When Bedrock merges
Java states the selector tells apart, the selection is refused rather than
guessed.
"""
from functools import lru_cache
import json
from pathlib import Path

STATE_TABLE = Path(__file__).resolve().parents[1] / 'converter/data/java-bedrock-states.json'


@lru_cache(maxsize=1)
def state_reference():
    """The state table: {'blocks': {Java block id: [{'java': states, 'native': {'block', 'states'}}]}}."""
    return json.loads(STATE_TABLE.read_text(encoding='utf-8'))


def native_block_java_ids():
    """{Bedrock block id: Java block id} for every Bedrock block in the table.

    A Bedrock block that stands for more than one Java block is refused: its
    Java identity would then depend on its states.
    """
    java_of = {}
    for java_block, rows in state_reference()['blocks'].items():
        for row in rows:
            bedrock_block = row['native']['block']
            if bedrock_block in java_of and java_of[bedrock_block] != java_block:
                raise ValueError('Native block ID requires state-dependent Java identity: ' + bedrock_block)
            java_of[bedrock_block] = java_block
    return java_of


def resolve_known_block_selection(block, values):
    """states_java_to_bedrock for a block the table lists; None only when it has no entry for the block."""
    if block not in state_reference()['blocks']:
        return None
    return states_java_to_bedrock(block, values)


def states_java_to_bedrock(block, values):
    """Every Bedrock selection that shows the Java states a selector allows.

    values: {state name: allowed value or list of values}. Returns
    [{'block', 'states'}] with the fewest state conditions that still pick
    exactly the allowed rows. Raises ValueError when the table has no entry for
    the block, when no row matches, or when one Bedrock selection stands for
    both allowed and excluded Java states.
    """
    block = block if ':' in block else 'minecraft:' + block
    rows = state_reference()['blocks'].get(block)
    if rows is None:
        raise ValueError('No verified Java state reference for ' + block)
    allowed = _allowed_values(values)
    selected, excluded = [], set()
    for row in rows:
        if all(str(row['java'].get(name)) in options for name, options in allowed.items()):
            selected.append(row['native'])
        else:
            excluded.add(_selection_key(row['native']))
    if not selected:
        raise ValueError('Java state selection matches no reference: ' + block + ' ' + str(values))
    if any(_selection_key(native) in excluded for native in selected):
        raise ValueError('Bedrock cannot distinguish this Java state predicate: ' + block + ' ' + str(values))
    return _fewest_conditions(selected, rows)


def _allowed_values(values):
    """{state name: allowed values as strings}; Java writes booleans as true and false."""
    allowed = {}
    for name, value in values.items():
        options = value if isinstance(value, (list, tuple, set)) else [value]
        allowed[name] = {str(option).lower() if isinstance(option, bool) else str(option) for option in options}
    return allowed


def _selection_key(native):
    """A Bedrock selection {'block', 'states'} in a hashable, sortable form."""
    return native['block'], tuple(sorted(native['states'].items()))


def _matches(states, conditions):
    return all(states.get(name) == value for name, value in conditions.items())


def _fewest_conditions(selected, rows):
    """The selected Bedrock rows as conditions without the states the selection does not depend on.

    A state is dropped when every row of the block that matches the remaining
    conditions is selected anyway. A row that an earlier, wider condition
    already covers adds nothing.
    """
    chosen = {_selection_key(native): native for native in selected}
    every_row = {_selection_key(row['native']): row['native'] for row in rows}
    result = []
    for _, native in sorted(chosen.items()):
        if any(existing['block'] == native['block'] and _matches(native['states'], existing['states'])
               for existing in result):
            continue
        conditions = dict(native['states'])
        for name in sorted(conditions):
            wider = {state: value for state, value in conditions.items() if state != name}
            if all(key in chosen for key, other in every_row.items()
                   if other['block'] == native['block'] and _matches(other['states'], wider)):
                conditions = wider
        result.append({'block': native['block'], 'states': conditions})
    return result
