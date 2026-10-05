"""Translate OptiFine/EMF entity-model expressions into Bedrock Molang.

OptiFine custom entity model animations are expressions over model variables
(``limb_swing``, ``head_yaw`` ...), entity variables (``var.*``, ``varb.*``)
and model-part values (``head.rx`` ...), with trigonometry in radians.
Molang works in degrees and exposes the entity through ``query.*``.

The translator is purely syntactic: it parses the OptiFine expression into a
tree, resolves every identifier through a caller-supplied resolver and prints
fully parenthesised Molang. Each language also has a small evaluator here: the
OptiFine one folds constant expressions and, with the Molang one, lets the
tests check a translation numerically on random inputs.

Expression trees are tuples:
    ('num', value)                       a number, always a float
    ('var', name)                        an identifier; dotted names stay whole ('head.rx')
    ('call', name, [arguments])          a function call (OptiFine names lower-cased)
    ('unary', operator, operand)         '-' or '!'
    ('bin', operator, left, right)       a binary operator
Parsed Molang adds ('str', text), ('cond', condition, if_true, if_false),
('block', [statements]) and ('assign', name, value).

OptiFine reference: https://github.com/sp614x/optifine/blob/master/OptiFineDoc/doc/cem_animation.txt
Molang reference: https://learn.microsoft.com/minecraft/creator/reference/content/molangreference/
"""
from __future__ import annotations

import math
import re

DEGREES_PER_RADIAN = 180.0 / math.pi
RADIANS_PER_DEGREE = math.pi / 180.0

# OptiFine/EMF entity variables with a Bedrock client query of the same meaning.
# Values are Molang expressions in OptiFine units (radians stay radians).
MODEL_VARIABLES = {
    'limb_swing': 'query.modified_distance_moved',
    'limb_speed': 'query.modified_move_speed',
    'head_yaw': 'query.target_y_rotation',
    'head_pitch': 'query.target_x_rotation',
    'age': '(query.life_time * 20)',
    'frame_time': 'query.delta_time',
    'swing_progress': 'variable.attack_time',
    'hurt_time': 'query.hurt_time',
    'health': 'query.health',
    'max_health': 'query.max_health',
    'pos_x': 'query.position(0)',
    'pos_y': 'query.position(1)',
    'pos_z': 'query.position(2)',
    'rot_y': '(query.body_y_rotation * 0.017453292519943295)',
    'rot_x': '(query.body_x_rotation * 0.017453292519943295)',
    'is_child': 'query.is_baby',
    'is_on_ground': 'query.is_on_ground',
    'is_in_water': 'query.is_in_water',
    'is_in_lava': 'query.is_in_lava',
    'is_wet': 'query.is_in_water_or_rain',
    'is_riding': 'query.is_riding',
    'is_ridden': 'query.has_rider',
    'is_sitting': 'query.is_sitting',
    'is_sneaking': 'query.is_sneaking',
    'is_sprinting': 'query.is_sprinting',
    'is_alive': 'query.is_alive',
    'is_burning': 'query.is_on_fire',
    'is_tamed': 'query.is_tamed',
    'is_aggressive': 'query.has_target',
    'is_hurt': '(query.hurt_time > 0)',
    'is_invisible': 'query.is_invisible',
    'is_glowing': '0',
    'time': 'query.time_stamp',
    'day_time': '(query.time_of_day * 24000)',
    'death_time': 'query.death_ticks',
    'wing_flap_position': 'query.wing_flap_position',
    'wing_flap_speed': 'query.wing_flap_speed',
    'pi': 'math.pi',
    'true': '1',
    'false': '0',
}
# Variables with no Bedrock client equivalent; they evaluate to the value OptiFine
# reports for an entity in the default state and every use is reported.
UNMAPPED_VARIABLES = {
    'rule_index': '0', 'anger_time': '0', 'anger_time_start': '0', 'is_on_shoulder': '0',
    'player_pos_x': 'query.position(0)', 'player_pos_y': 'query.position(1)',
    'player_pos_z': 'query.position(2)', 'player_rot_x': '0', 'player_rot_y': '0',
    'is_in_hand': '0', 'is_in_item_frame': '0', 'is_in_ground': '0', 'is_on_head': '0',
    'dimension': '0', 'move_forward': '0', 'move_strafing': '0', 'is_jumping': '0',
    'is_climbing': '0', 'is_blocking': '0', 'is_using_item': '0', 'is_holding_item': '0',
    'is_right_handed': '1', 'distance': 'query.distance_from_camera',
}

_OPTIFINE_TOKEN = re.compile(r'''
    \s*
    (?:
        (?P<number> (?:\d+\.\d*|\.\d+|\d+) (?:[eE][-+]?\d+)? )
      | (?P<identifier> [A-Za-z_][A-Za-z_0-9]* (?:\.[A-Za-z_][A-Za-z_0-9]*)* )
      | (?P<operator> &&|\|\||==|!=|<=|>=|[-+*/%(),!<>?:] )
    )''', re.VERBOSE)
# Binding strength of the OptiFine binary operators (higher binds tighter); all associate left.
_OPTIFINE_PRECEDENCE = {'||': 1, '&&': 2, '==': 3, '!=': 3, '<': 4, '<=': 4, '>': 4, '>=': 4,
                        '+': 5, '-': 5, '*': 6, '/': 6, '%': 6}
_CONSTANT_NAMES = ('pi', 'true', 'false')
_RANDOM_FUNCTIONS = ('random', 'randomb')
# A seeded random(seed) is computed as the classic sine hash frac(sin(seed * multiplier) * amplitude):
# repeatable per seed and expressible in Molang, so the evaluator and the translation agree.
_HASH_MULTIPLIER = 12.9898
_HASH_AMPLITUDE = 43758.5453

_MOLANG_TOKEN = re.compile(r'''
    \s*
    (?:
        (?P<number> \d+\.\d*(?:[eE][-+]?\d+)? | \.\d+ | \d+(?:[eE][-+]?\d+)? ) [fF]?
      | (?P<identifier> [A-Za-z_][A-Za-z_0-9]* (?:\.[A-Za-z_][A-Za-z_0-9]*)* )
      | (?P<operator> &&|\|\||==|!=|<=|>=|\?\?|[-+*/(),!<>?:=;{}] )
      | '(?P<string>[^']*)'
    )''', re.VERBOSE)
_MOLANG_PRECEDENCE = {'??': 0, '||': 1, '&&': 2, '==': 3, '!=': 3, '<': 4, '<=': 4, '>': 4, '>=': 4,
                      '+': 5, '-': 5, '*': 6, '/': 6}
_MOLANG_NAMESPACE_ALIASES = {'v': 'variable', 't': 'temp', 'q': 'query', 'c': 'context'}


class ExpressionError(ValueError):
    """An expression that cannot be parsed, translated or evaluated."""


# --- OptiFine parsing ---

def tokenize(text):
    """Split an OptiFine expression into ('num', value), ('id', name) and ('op', symbol) tokens."""
    text = str(text)
    tokens = []
    position = 0
    while position < len(text):
        if text[position:].strip() == '':
            break
        match = _OPTIFINE_TOKEN.match(text, position)
        if not match:
            raise ExpressionError('Unexpected character in expression: ' + text[position:position + 12])
        if match.group('number') is not None:
            tokens.append(('num', float(match.group('number'))))
        elif match.group('identifier') is not None:
            tokens.append(('id', match.group('identifier')))
        else:
            tokens.append(('op', match.group('operator')))
        position = match.end()
    return tokens


def parse(text):
    """Parse an OptiFine expression (text, or a JSON number or boolean) into an expression tree."""
    if isinstance(text, bool):
        return ('num', 1.0 if text else 0.0)
    if isinstance(text, (int, float)):
        return ('num', float(text))
    return _OptiFineParser(text).parse()


class _OptiFineParser:
    """Recursive-descent parser for one OptiFine expression, with precedence climbing for binary operators."""

    def __init__(self, text):
        self.text = text            # as the caller gave it, for error messages
        self.tokens = tokenize(text)
        self.index = 0

    def parse(self):
        if not self.tokens:
            raise ExpressionError('Empty expression')
        node = self._expression(0)
        if self.index != len(self.tokens):
            raise ExpressionError(f'Unexpected trailing tokens in {self.text!r}')
        return node

    def _peek(self):
        if self.index < len(self.tokens):
            return self.tokens[self.index]
        return (None, None)

    def _take(self, expected_operator=None):
        token = self._peek()
        if expected_operator is not None and token != ('op', expected_operator):
            raise ExpressionError(f'Expected {expected_operator!r} in {self.text!r}')
        self.index += 1
        return token

    def _expression(self, minimum_precedence):
        left = self._primary()
        while True:
            kind, operator = self._peek()
            if (kind != 'op' or operator not in _OPTIFINE_PRECEDENCE
                    or _OPTIFINE_PRECEDENCE[operator] < minimum_precedence):
                return left
            self._take()
            right = self._expression(_OPTIFINE_PRECEDENCE[operator] + 1)
            left = ('bin', operator, left, right)

    def _primary(self):
        kind, value = self._peek()
        if kind == 'num':
            self._take()
            return ('num', value)
        if kind == 'op' and value in ('-', '+', '!'):
            # Unary operators bind tighter than any binary one; a unary plus changes nothing.
            self._take()
            operand = self._primary()
            return operand if value == '+' else ('unary', value, operand)
        if kind == 'op' and value == '(':
            self._take()
            node = self._expression(0)
            self._take(')')
            return node
        if kind == 'id':
            self._take()
            if self._peek() == ('op', '('):
                return ('call', value.lower(), self._call_arguments())
            return ('var', value)
        raise ExpressionError(f'Unexpected token {value!r} in {self.text!r}')

    def _call_arguments(self):
        self._take('(')
        arguments = []
        if self._peek() != ('op', ')'):
            arguments.append(self._expression(0))
            while self._peek() == ('op', ','):
                self._take()
                arguments.append(self._expression(0))
        self._take(')')
        return arguments


def identifiers(node, found=None):
    """Every identifier an expression tree reads, added to found (a new set by default)."""
    found = set() if found is None else found
    kind = node[0]
    if kind == 'var':
        found.add(node[1])
    elif kind == 'call':
        for argument in node[2]:
            identifiers(argument, found)
    elif kind == 'unary':
        identifiers(node[2], found)
    elif kind == 'bin':
        identifiers(node[2], found)
        identifiers(node[3], found)
    return found


def is_constant(node):
    """True when a tree reads no variables and draws no random numbers, so it folds to one value."""
    kind = node[0]
    if kind == 'num':
        return True
    if kind == 'var':
        return node[1] in _CONSTANT_NAMES
    if kind == 'call':
        return node[1] not in _RANDOM_FUNCTIONS and all(is_constant(argument) for argument in node[2])
    if kind == 'unary':
        return is_constant(node[2])
    return is_constant(node[2]) and is_constant(node[3])    # 'bin'


# --- OptiFine evaluation (reference semantics for tests and constant folding) ---

def evaluate(node, environment, random_value=0.5):
    """Evaluate a tree with OptiFine semantics; environment maps identifiers to numbers.

    random() without a seed returns random_value, so results repeat.
    """
    kind = node[0]
    if kind == 'num':
        return node[1]
    if kind == 'var':
        return _variable_value(node[1], environment)
    if kind == 'unary':
        value = evaluate(node[2], environment, random_value)
        return -value if node[1] == '-' else float(not _truth(value))
    if kind == 'bin':
        return _evaluate_binary(node[1], node[2], node[3], environment, random_value)
    return _evaluate_call(node[1], node[2], environment, random_value)


def _truth(value):
    return value != 0


def _variable_value(name, environment):
    if name == 'pi':
        return math.pi
    if name in ('true', 'false'):
        return 1.0 if name == 'true' else 0.0
    if name not in environment:
        raise ExpressionError('Unbound variable: ' + name)
    return float(environment[name])


def _java_divide(dividend, divisor):
    if divisor:
        return dividend / divisor
    # Java float division by zero gives an infinity of the dividend's sign; 0 / 0 is taken as 0.
    if dividend > 0:
        return math.inf
    if dividend < 0:
        return -math.inf
    return 0.0


def _java_remainder(dividend, divisor):
    # Java's % keeps the dividend's sign, as math.fmod does; x % 0 is taken as 0.
    return math.fmod(dividend, divisor) if divisor else 0.0


_OPTIFINE_BINARY_OPERATIONS = {
    '+': lambda left, right: left + right,
    '-': lambda left, right: left - right,
    '*': lambda left, right: left * right,
    '/': _java_divide,
    '%': _java_remainder,
    '<': lambda left, right: float(left < right),
    '<=': lambda left, right: float(left <= right),
    '>': lambda left, right: float(left > right),
    '>=': lambda left, right: float(left >= right),
    '==': lambda left, right: float(left == right),
    '!=': lambda left, right: float(left != right),
}


def _evaluate_binary(operator, left, right, environment, random_value):
    # && and || only evaluate their right side when it decides the result.
    if operator == '&&':
        return float(_truth(evaluate(left, environment, random_value))
                     and _truth(evaluate(right, environment, random_value)))
    if operator == '||':
        return float(_truth(evaluate(left, environment, random_value))
                     or _truth(evaluate(right, environment, random_value)))
    left_value = evaluate(left, environment, random_value)
    right_value = evaluate(right, environment, random_value)
    return _OPTIFINE_BINARY_OPERATIONS[operator](left_value, right_value)


_OPTIFINE_ONE_ARGUMENT_FUNCTIONS = {
    'sin': math.sin, 'cos': math.cos, 'tan': math.tan, 'asin': math.asin, 'acos': math.acos,
    'atan': math.atan, 'abs': abs, 'floor': math.floor, 'ceil': math.ceil, 'exp': math.exp,
    'log': math.log, 'sqrt': math.sqrt,
    'round': lambda value: math.floor(value + 0.5),     # Java's Math.round: halves round up
    'torad': lambda value: value * RADIANS_PER_DEGREE,
    'todeg': lambda value: value * DEGREES_PER_RADIAN,
    'frac': lambda value: value - math.floor(value),
    'signum': lambda value: float((value > 0) - (value < 0)),
}


def _evaluate_call(name, arguments, environment, random_value):
    if name in ('if', 'ifb'):
        return _evaluate_if(arguments, environment, random_value)
    values = [evaluate(argument, environment, random_value) for argument in arguments]
    if name in _OPTIFINE_ONE_ARGUMENT_FUNCTIONS:
        return float(_OPTIFINE_ONE_ARGUMENT_FUNCTIONS[name](values[0]))
    if name == 'atan2':
        return math.atan2(values[0], values[1])
    if name == 'pow':
        return math.pow(values[0], values[1])
    if name == 'fmod':
        # Floored remainder (the divisor's sign, like Java's Math.floorMod), unlike %.
        return values[0] - values[1] * math.floor(values[0] / values[1]) if values[1] else 0.0
    if name == 'min':
        return min(values)
    if name == 'max':
        return max(values)
    if name == 'clamp':
        return min(max(values[0], values[1]), values[2])
    if name == 'lerp':
        # lerp(amount, start, end)
        return values[1] + values[0] * (values[2] - values[1])
    if name == 'between':
        # between(value, low, high)
        return float(values[1] <= values[0] <= values[2])
    if name == 'equals':
        # equals(value, other, epsilon)
        return float(abs(values[0] - values[1]) <= values[2])
    if name == 'in':
        # in(value, candidate, candidate...)
        return float(any(values[0] == other for other in values[1:]))
    if name in _RANDOM_FUNCTIONS:
        return _seeded_random(values[0]) if values else random_value
    if name in ('print', 'printb'):
        # print only logs in OptiFine; its value is the last argument.
        return values[-1]
    raise ExpressionError('Unsupported function: ' + name)


def _evaluate_if(arguments, environment, random_value):
    """if(condition, value, condition, value, ..., otherwise): only the chosen value is evaluated."""
    for index in range(0, len(arguments) - 1, 2):
        if _truth(evaluate(arguments[index], environment, random_value)):
            return evaluate(arguments[index + 1], environment, random_value)
    if len(arguments) % 2:
        return evaluate(arguments[-1], environment, random_value)
    return 0.0


def _seeded_random(seed):
    value = math.sin(seed * _HASH_MULTIPLIER) * _HASH_AMPLITUDE
    return value - math.floor(value)


# --- Molang emission ---

def translate(text, resolve):
    """Return (molang, unresolved identifiers, approximations) for an OptiFine expression."""
    translator = Translator(resolve)
    molang = translator.emit(parse(text))
    return molang, translator.unresolved, translator.approximations


class Translator:
    """Print an expression tree as Molang.

    resolve(name) returns the Molang text for an identifier, or None when the
    identifier is unknown; unknown names are recorded in unresolved and
    translated as 0. approximations collects what Molang can only approximate.
    """

    def __init__(self, resolve):
        self.resolve = resolve
        self.unresolved = set()
        self.approximations = set()

    def emit(self, node):
        """Molang text of an expression tree."""
        kind = node[0]
        if kind == 'num':
            text = _number_text(node[1])
            # A negative literal is parenthesised so it can follow any operator.
            return text if node[1] >= 0 else '(' + text + ')'
        if kind == 'var':
            return self._emit_identifier(node[1])
        if kind == 'unary':
            operand = self.emit(node[2])
            return '(-' + operand + ')' if node[1] == '-' else '(!' + operand + ')'
        if kind == 'bin':
            return self._emit_binary(node[1], node[2], node[3])
        return self._emit_call(node[1], node[2])

    def _emit_identifier(self, name):
        text = self.resolve(name)
        if text is None:
            self.unresolved.add(name)
            return '0'
        return text

    def _emit_binary(self, operator, left, right):
        left_text = self.emit(left)
        right_text = self.emit(right)
        if operator == '%':
            return _molang_call('math.mod', left_text, right_text)
        # Every operation is parenthesised, so the OptiFine grouping survives as written.
        return '(' + left_text + ' ' + operator + ' ' + right_text + ')'

    def _emit_call(self, name, arguments):
        if name in ('sin', 'cos') and len(arguments) == 1 and _is_call_of(arguments[0], 'torad'):
            # sin(torad(x)) is common; Molang's trigonometry takes degrees, so x passes through exactly.
            return _molang_call('math.' + name, self.emit(arguments[0][2][0]))
        emitted = [self.emit(argument) for argument in arguments]
        if name in _RANDOM_FUNCTIONS:
            return self._emit_random(emitted)
        return _function_text(name, emitted)

    def _emit_random(self, arguments):
        if not arguments:
            self.approximations.add('random() without a seed draws a new value each frame')
            return 'math.random(0, 1)'
        # The evaluator's seeded hash; its sine argument is in radians, converted for Molang's math.sin.
        seed = '(' + arguments[0] + ') * ' + repr(_HASH_MULTIPLIER)
        value = _molang_call('math.sin', _to_degrees(seed)) + ' * ' + repr(_HASH_AMPLITUDE)
        return '((' + value + ') - ' + _molang_call('math.floor', value) + ')'


def _function_text(name, arguments):
    """Molang for an OptiFine function call whose arguments are already Molang text."""
    # Trigonometry: OptiFine works in radians, Molang in degrees.
    if name in ('sin', 'cos'):
        return _molang_call('math.' + name, _to_degrees(arguments[0]))
    if name == 'tan':
        # Molang has no tangent function.
        angle = _to_degrees(arguments[0])
        return '(' + _molang_call('math.sin', angle) + ' / ' + _molang_call('math.cos', angle) + ')'
    if name in ('asin', 'acos', 'atan'):
        return _to_radians(_molang_call('math.' + name, arguments[0]))
    if name == 'atan2':
        return _to_radians(_molang_call('math.atan2', arguments[0], arguments[1]))
    if name == 'torad':
        return _to_radians(arguments[0])
    if name == 'todeg':
        return _to_degrees(arguments[0])
    # Arithmetic.
    if name in ('abs', 'floor', 'ceil', 'exp', 'sqrt'):
        return _molang_call('math.' + name, arguments[0])
    if name == 'round':
        # Java's Math.round: halves round up.
        return _molang_call('math.floor', '(' + arguments[0] + ') + 0.5')
    if name == 'log':
        return _molang_call('math.ln', arguments[0])
    if name == 'pow':
        return _molang_call('math.pow', arguments[0], arguments[1])
    if name == 'frac':
        return '((' + arguments[0] + ') - ' + _molang_call('math.floor', arguments[0]) + ')'
    if name == 'signum':
        return '((' + arguments[0] + ' > 0) - (' + arguments[0] + ' < 0))'
    if name == 'fmod':
        # Floored remainder: dividend - divisor * floor(dividend / divisor).
        dividend, divisor = arguments
        quotient = _molang_call('math.floor', '(' + dividend + ') / (' + divisor + ')')
        return '((' + dividend + ') - (' + divisor + ') * ' + quotient + ')'
    if name in ('min', 'max'):
        # Molang's math.min and math.max take two values: fold the list pairwise.
        result = arguments[0]
        for other in arguments[1:]:
            result = _molang_call('math.' + name, result, other)
        return result
    if name == 'clamp':
        return _molang_call('math.clamp', *arguments[:3])
    if name == 'lerp':
        # OptiFine lerp(amount, start, end) is Molang math.lerp(start, end, amount).
        return _molang_call('math.lerp', arguments[1], arguments[2], arguments[0])
    # Comparisons and conditions.
    if name == 'between':
        return '((' + arguments[0] + ' >= ' + arguments[1] + ') && (' + arguments[0] + ' <= ' + arguments[2] + '))'
    if name == 'equals':
        difference = _molang_call('math.abs', arguments[0] + ' - ' + arguments[1])
        return '(' + difference + ' <= ' + arguments[2] + ')'
    if name == 'in':
        if len(arguments) <= 1:
            return '0'
        return '(' + ' || '.join('(' + arguments[0] + ' == ' + other + ')' for other in arguments[1:]) + ')'
    if name in ('if', 'ifb'):
        return _conditional_text(arguments)
    if name in ('print', 'printb'):
        # print only logs in OptiFine; its value is the last argument.
        return arguments[-1]
    raise ExpressionError('Unsupported function: ' + name)


def _conditional_text(arguments):
    """if(condition, value, ..., otherwise) as nested Molang ternaries; a missing otherwise is 0."""
    if len(arguments) % 2 == 0:
        arguments = arguments + ['0']
    result = arguments[-1]
    for index in range(len(arguments) - 3, -1, -2):
        result = '(' + arguments[index] + ' ? ' + arguments[index + 1] + ' : ' + result + ')'
    return result


def _is_call_of(node, function):
    return node[0] == 'call' and node[1] == function


def _number_text(value):
    """A Molang number literal; whole numbers are written without a fraction."""
    if value == int(value) and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


def _molang_call(function, *arguments):
    return function + '(' + ', '.join(arguments) + ')'


def _to_degrees(radians_text):
    return '(' + radians_text + ') * ' + repr(DEGREES_PER_RADIAN)


def _to_radians(degrees_text):
    return '(' + degrees_text + ') * ' + repr(RADIANS_PER_DEGREE)


# --- Molang parsing and evaluation (a subset, to validate and test generated output) ---

def molang_valid(text):
    """True when text parses as Molang statements."""
    try:
        molang_parse(text)
        return True
    except ExpressionError:
        return False


def molang_parse(text):
    """Parse Molang statements (separated by ';') into a list of trees."""
    return _MolangParser(text).statements()


def molang_evaluate(text, environment, *, random_value=0.5):
    """Run Molang statements and return the last value; assignments update environment.

    environment maps full names ('variable.x', 'query.is_baby') to numbers. A query
    call is looked up as a callable under its name or by its text ('query.position(1)').
    math.random draws random_value, so results repeat.
    """
    evaluator = _MolangEvaluator(environment, random_value)
    result = 0.0
    for statement in molang_parse(text):
        result = evaluator.run(statement)
    return result


def _molang_tokens(text):
    tokens = []
    position = 0
    while position < len(text):
        if text[position:].strip() == '':
            break
        match = _MOLANG_TOKEN.match(text, position)
        if not match:
            raise ExpressionError('Invalid Molang near: ' + text[position:position + 16])
        if match.group('number') is not None:
            tokens.append(('num', float(match.group('number'))))
        elif match.group('identifier') is not None:
            tokens.append(('id', match.group('identifier')))
        elif match.group('operator') is not None:
            tokens.append(('op', match.group('operator')))
        else:
            tokens.append(('str', match.group('string')))
        position = match.end()
    return tokens


def _canonical_name(name):
    """Molang namespaces are case-insensitive and have short forms (v. for variable.); the rest is kept."""
    namespace, _, rest = name.partition('.')
    namespace = _MOLANG_NAMESPACE_ALIASES.get(namespace.lower(), namespace.lower())
    return namespace + ('.' + rest if rest else '')


class _MolangParser:
    """Recursive-descent parser for Molang statements, brace scopes and the ternary operator."""

    def __init__(self, text):
        self.text = text
        self.tokens = _molang_tokens(text)
        self.index = 0

    def statements(self):
        statements = []
        while self.index < len(self.tokens):
            if self._peek() == ('op', ';'):
                self._take()
                continue
            statements.append(self._statement())
            if self.index < len(self.tokens):
                self._take(';')
        return statements

    def _peek(self):
        if self.index < len(self.tokens):
            return self.tokens[self.index]
        return (None, None)

    def _take(self, expected_operator=None):
        token = self._peek()
        if expected_operator is not None and token != ('op', expected_operator):
            raise ExpressionError(f'Expected {expected_operator!r} in Molang {self.text[:60]!r}')
        self.index += 1
        return token

    def _statement(self):
        kind, value = self._peek()
        is_assignment = (kind == 'id' and self.index + 1 < len(self.tokens)
                         and self.tokens[self.index + 1] == ('op', '='))
        if is_assignment:
            self._take()
            self._take('=')
            return ('assign', _canonical_name(value), self._ternary())
        return self._ternary()

    def _ternary(self):
        condition = self._binary(0)
        if self._peek() != ('op', '?'):
            return condition
        self._take()
        if_true = self._ternary()
        if self._peek() != ('op', ':'):
            # 'condition ? value' without a second branch is 0 when the condition fails.
            return ('cond', condition, if_true, ('num', 0.0))
        self._take(':')
        return ('cond', condition, if_true, self._ternary())

    def _binary(self, minimum_precedence):
        left = self._primary()
        while True:
            kind, operator = self._peek()
            if (kind != 'op' or operator not in _MOLANG_PRECEDENCE
                    or _MOLANG_PRECEDENCE[operator] < minimum_precedence):
                return left
            self._take()
            left = ('bin', operator, left, self._binary(_MOLANG_PRECEDENCE[operator] + 1))

    def _primary(self):
        kind, value = self._peek()
        if kind == 'num':
            self._take()
            return ('num', value)
        if kind == 'str':
            self._take()
            return ('str', value)
        if kind == 'op' and value in ('-', '!'):
            self._take()
            return ('unary', value, self._primary())
        if kind == 'op' and value == '(':
            self._take()
            node = self._ternary()
            self._take(')')
            return node
        if kind == 'op' and value == '{':
            return self._block()
        if kind == 'id':
            self._take()
            if self._peek() == ('op', '('):
                return ('call', _canonical_name(value), self._call_arguments())
            return ('var', _canonical_name(value))
        raise ExpressionError(f'Unexpected Molang token {value!r}')

    def _block(self):
        self._take('{')
        body = []
        while self._peek() != ('op', '}'):
            if self._peek() == (None, None):
                raise ExpressionError('Unclosed brace scope in Molang')
            if self._peek() == ('op', ';'):
                self._take()
                continue
            body.append(self._statement())
        self._take('}')
        return ('block', body)

    def _call_arguments(self):
        self._take('(')
        arguments = []
        if self._peek() != ('op', ')'):
            arguments.append(self._ternary())
            while self._peek() == ('op', ','):
                self._take()
                arguments.append(self._ternary())
        self._take(')')
        return arguments


# The Molang math functions the reference evaluator knows (angles in degrees, like Molang).
_MOLANG_MATH = {
    'math.sin': lambda degrees: math.sin(degrees * RADIANS_PER_DEGREE),
    'math.cos': lambda degrees: math.cos(degrees * RADIANS_PER_DEGREE),
    'math.asin': lambda value: math.asin(value) * DEGREES_PER_RADIAN,
    'math.acos': lambda value: math.acos(value) * DEGREES_PER_RADIAN,
    'math.atan': lambda value: math.atan(value) * DEGREES_PER_RADIAN,
    'math.atan2': lambda y, x: math.atan2(y, x) * DEGREES_PER_RADIAN,
    'math.abs': abs,
    'math.floor': math.floor,
    'math.ceil': math.ceil,
    'math.round': lambda value: math.floor(value + 0.5),
    'math.trunc': math.trunc,
    'math.sqrt': math.sqrt,
    'math.exp': math.exp,
    'math.ln': math.log,
    'math.pow': math.pow,
    'math.min': min,
    'math.max': max,
    'math.clamp': lambda value, low, high: min(max(value, low), high),
    'math.mod': lambda value, denominator: math.fmod(value, denominator) if denominator else 0.0,
    'math.lerp': lambda start, end, amount: start + (end - start) * amount,
}


class _MolangEvaluator:
    """Runs parsed Molang statements against an environment of variables and query values."""

    def __init__(self, environment, random_value):
        self.environment = environment
        self.functions = dict(_MOLANG_MATH)
        self.functions['math.random'] = lambda low, high: low + (high - low) * random_value

    def run(self, statement):
        if statement[0] == 'assign':
            value = self.value(statement[2])
            self.environment[statement[1]] = value
            return value
        return self.value(statement)

    def value(self, node):
        kind = node[0]
        if kind in ('num', 'str'):
            return node[1]
        if kind == 'var':
            if node[1] == 'math.pi':
                return math.pi
            # A variable that was never set reads as 0, as in the game.
            return float(self.environment.get(node[1], 0.0))
        if kind == 'unary':
            operand = self.value(node[2])
            return -operand if node[1] == '-' else float(operand == 0)
        if kind == 'cond':
            return self.value(node[2]) if self.value(node[1]) != 0 else self.value(node[3])
        if kind == 'block':
            result = 0.0
            for statement in node[1]:
                result = self.run(statement)
            return result
        if kind == 'bin':
            return self._binary(node[1], node[2], node[3])
        return self._call(node[1], [self.value(argument) for argument in node[2]])

    def _binary(self, operator, left, right):
        if operator == '&&':
            return float(self.value(left) != 0 and self.value(right) != 0)
        if operator == '||':
            return float(self.value(left) != 0 or self.value(right) != 0)
        if operator == '??':
            # 'name ?? fallback' reads the variable when it is set, else the fallback.
            name = left[1] if left[0] == 'var' else None
            return float(self.environment[name]) if name in self.environment else self.value(right)
        left_value = self.value(left)
        right_value = self.value(right)
        results = {'+': left_value + right_value, '-': left_value - right_value, '*': left_value * right_value,
                   '/': (left_value / right_value) if right_value else 0.0,
                   '<': float(left_value < right_value), '<=': float(left_value <= right_value),
                   '>': float(left_value > right_value), '>=': float(left_value >= right_value),
                   '==': float(left_value == right_value), '!=': float(left_value != right_value)}
        return results[operator]

    def _call(self, name, arguments):
        if name.startswith('query.'):
            return self._query(name, arguments)
        if name not in self.functions:
            raise ExpressionError('Unsupported Molang function: ' + name)
        return float(self.functions[name](*arguments))

    def _query(self, name, arguments):
        if name in self.environment and callable(self.environment[name]):
            return float(self.environment[name](*arguments))
        key = name + '(' + ','.join(_number_text(argument) for argument in arguments) + ')'
        if key in self.environment:
            return float(self.environment[key])
        raise ExpressionError('Unbound Molang query call: ' + key)
