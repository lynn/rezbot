import unicodedata2
import emoji
import utils.util as util
import random
import math
import textwrap
import re
from functools import wraps, lru_cache

from datamuse import datamuse
datamuse_api = datamuse.Datamuse()
from google.cloud import translate
import nltk

from .signature import Sig
from .pipe import Pipe, Pipes
from utils.texttools import vowelize, consonize, letterize, letterize2, converters, min_dist, case_pattern
from utils.rand import choose_slice
from utils.util import parse_bool
from resource.upload import uploads

#######################################################
#                     Decorations                     #
#######################################################

def as_map(func):
    '''
    Decorate a function to accept an array of first arguments:

    f: (x, *args) -> y      becomes     f': ([x], args) -> [y]
    e.g.
    pow(3, 2) -> 9          becomes     pow'([3, 4, 5], 2) -> [9, 16, 25]
    '''
    @wraps(func)
    def _as_map(input, *args, **kwargs):
        return [func(i, *args, **kwargs) for i in input]
    return _as_map

_word_splitter = re.compile(r'([\w\'-]+)') # groups together words made out of letters or ' or -

def word_map(func):
    '''
    Decorator allowing a pipe to treat input on a word-by-word basis, with symbols etc. removed.
    '''
    @wraps(func)
    def _word_map(line, *args, **kwargs):
        split = re.split(_word_splitter, line)
        for i in range(1, len(split), 2):
            split[i] = func(split[i], *args, **kwargs)
        return ''.join(split)
    return as_map(_word_map)

pipes = Pipes()
pipes.command_pipes = []
_CATEGORY = 'NONE'

def make_pipe(signature, command=False):
    '''Makes a Pipe out of a function.'''
    def _make_pipe(func):
        global pipes, _CATEGORY
        pipe = Pipe(signature, func, _CATEGORY)
        pipes.add(pipe)
        if command:
            pipes.command_pipes.append(pipe)
        return func
    return _make_pipe


#####################################################
#                       Pipes                       #
#####################################################

#####################################################
#                   Pipes : FLOW                    #
#####################################################
_CATEGORY = 'FLOW'

@make_pipe( {
    'times': Sig(int, None, 'Number of times repeated'),
    'max'  : Sig(int, -1, 'Maximum number of outputs produced, -1 for unlimited.')
})
def repeat_pipe(input, times, max):
    '''Repeats each row a given number of times.'''
    # Isn't decorated as_map so both input and output are expected to be arrays.
    if max == -1:
        return input * times
    else:
        times = min(times, math.ceil(max/len(input))) # Limit how many unnecessary items the [:max] in the next line shaves off
        return (input*times)[:max]


@make_pipe({ 'what': Sig(str, 'all', 'What to filter: all/empty/whitespace', options=['all', 'empty', 'whitespace']) })
def remove_pipe(input, what):
    '''
    Removes all items (or specific types of items) from the flow.

    all: Removes every item
    whitespace: Removes items that only consist of whitespace (including empty ones)
    empty: Only removes items equal to the empty string ("")
    '''
    what = what.lower()
    if what == 'all':
        return []
    if what == 'whitespace':
        return [x for x in input if x.strip() != '']
    if what == 'empty':
        return [x for x in input if x != '']


@make_pipe({})
def sort_pipe(input):
    '''Sorts the input values lexicographically.'''
    # IMPORTANT: `input` is passed BY REFERENCE, so we are NOT supposed to mess with it!
    out = input[:]
    out.sort()
    return out


@make_pipe({})
def shuffle_pipe(input):
    '''Randomly shuffles input values.'''
    # IMPORTANT NOTE: `input` is passed BY REFERENCE, so we are NOT supposed to mess with it!
    out = input[:]
    random.shuffle(out)
    return out


@make_pipe({ 'number' : Sig(int, 1, 'The number of values to choose.', lambda x: x>=0) })
def choose_pipe(input, number):
    '''Chooses random values with replacement (i.e. may return repeated values).'''
    return random.choices(input, k=number)


@make_pipe({ 'number' : Sig(int, 1, 'The number of values to sample.', lambda x: x>=0) })
def sample_pipe(input, number):
    '''
    Chooses random values without replacement.
    Never produces more values than the number it receives.
    '''
    return random.sample(input, min(len(input), number))


@make_pipe({
    'length' : Sig(int, None, 'The desired slice length.', lambda x: x>=0),
    'cyclical': Sig(parse_bool, False, 'Whether or not the slice is allowed to "loop back" to cover both some first elements and last elements. ' +
            'i.e. If False, elements at the start and end of the input have lower chance of being selected, if True all elements have an equal chance.')
})
def choose_slice_pipe(input, length, cyclical):
    '''Randomly chooses a contiguous sequence of items of the given length.'''
    return choose_slice(input, length, cyclical=cyclical)


@make_pipe({ 'count' : Sig(parse_bool, False, 'Whether each unique item should be followed by a count of how many there were of it.') })
def unique_pipe(input, count):
    '''Returns the first unique occurence of each value.'''
    if not count: return [*{*input}]

    values = []
    counts = []
    for value in input:
        try:
            counts[values.index(value)] += 1
        except:
            values.append(value)
            counts.append(1)
    counts = map(str, counts)
    return [x for tup in zip(values, counts) for x in tup]


@make_pipe({})
def reverse_pipe(input):
    '''Reverses the order of the input values.'''
    return input[::-1]


@make_pipe({})
def count_pipe(input):
    '''Counts the number of input values it receives.'''
    return [str(len(input))]


#####################################################
#                  Pipes : STRING                   #
#####################################################
_CATEGORY = 'STRING'

@make_pipe({
    'on' : Sig(str, r'\s*\n+\s*', 'Pattern to split on (regex)'),
    'lim': Sig(int, 0, 'Maximum number of splits. (0 for no limit)')
})
def split_pipe(inputs, on, lim):
    '''Split the input into multiple outputs.'''
    return [x for y in inputs for x in re.split(on, y, maxsplit=lim)]


@make_pipe({
    'width': Sig(int, None, 'How many characters to trim each string down to.'),
    'where': Sig(str, 'right', 'Which side to trim from: left/center/right', options=['left', 'center', 'right']),
})
@as_map
def trim_pipe(text, width, where):
    '''Trim the input to a certain width, discarding the rest.'''
    where = where.lower()
    if where == 'left':
        return text[-width:]
    if where == 'right':
        return text[:width]
    if where == 'center':
        diff = max(0, len(text)-width)
        return text[ diff//2 : -math.ceil(diff/2) ]

@make_pipe({
    'width': Sig(int, None, 'The minimum width to pad to.'),
    'where': Sig(str, 'right', 'Which side to pad on: left/center/right', options=['left', 'center', 'right']),
    'fill' : Sig(str, ' ', 'The character used to pad out the string.'),
})
@as_map
def pad_pipe(text, where, width, fill):
    '''Pad the input to a certain width.'''
    where = where.lower()
    if where == 'left':
        return text.rjust(width, fill)
    if where == 'center':
        return text.center(width, fill)
    if where == 'right':
        return text.ljust(width, fill)


@make_pipe({
    'mode' : Sig(str, 'smart', 'How to wrap: dumb (char-by-char) or smart (on spaces).', options=['dumb', 'smart']),
    'width': Sig(int, 40, 'The minimum width to pad to.')
})
def wrap_pipe(inputs, mode, width):
    '''Text wrap the input: Split the input into multiple lines so that each line is shorter than a certain width.'''
    mode = mode.lower()
    if mode == 'dumb':
        return [text[i:i+width] for text in inputs for i in range(0, len(text), width)]
    if mode == 'smart':
        return [wrapped for text in inputs for wrapped in textwrap.wrap(text, width)]


@make_pipe({})
@as_map
def strip_pipe(value):
    '''Strips whitespace from the starts and ends of each input.'''
    return value.strip()


@make_pipe({
    'from': Sig(str, None, 'Pattern to replace (regex)'),
    'to' : Sig(str, None, 'Replacement string'),
})
@as_map
def sub_pipe(text, to, **argc):
    '''Substitutes patterns in the input.'''
    return re.sub(argc['from'], to, text)


@make_pipe({
    'pattern': Sig(str, None, 'Case pattern to obey'),
})
def case_pipe(text, pattern):
    '''
    Converts the case of each input according to a pattern.

    A pattern is parsed as a sequence 4 types of actions:
    • Upper/lowercase characters (A/a) enforce upper/lowercase
    • Neutral characters (?!_-,.etc.) leave case unchanged
    • Carrot (^) swaps upper to lowercase and the other way around

    Furthermore, parentheseses will repeat that part to stretch the pattern to fit the entire input.

    Examples:
        A       Just turns the first character uppercase
        Aa      Turns the first character upper, the second lower
        A(a)    Turns the first character upper, all others lower
        A(-)A   Turns the first upper, the last lower
        ^(Aa)^  Reverses the first and last characters, AnD DoEs tHiS To tHe oNeS BeTwEeN
    '''
    return case_pattern(pattern, *text)


@make_pipe({
    'f' : Sig(str, None, 'The format string. Items of the form {0}, {1} etc. are replaced with the respective item at that index.')
})
def format_pipe(input, f):
    '''Format one or more rows into a single row according to a format string.'''
    # return [f.format(*input)]
    return [f]
    # The formatting already happens beforehand, but trying to do it again risks messing things up

@make_pipe({
    's' : Sig(str, '', 'The separator inserted between two items.')
})
def join_pipe(input, s):
    '''Joins rows into a single row, separated by the given separator.'''
    return [s.join(input)]


@make_pipe({
    'columns': Sig(str, None, 'The names of the different columns separated by commas, or an integer giving the number of columns.'),
    'alignments': Sig(str, 'l', 'The way the columns should be aligned: l/c/r separated by commas.', options=['l', 'c', 'r'], multi_options=True),
    'code_block': Sig(parse_bool, True, 'Whether or not the table should already be wrapped in a Discord code block.')
})
def table_pipe(input, columns, alignments, code_block):
    '''Formats data as a table'''
    try:
        colNames = None
        colCount = int(columns)
    except:
        colNames = columns.split(',')
        colCount = len(colNames)

    if colCount <= 0:
        raise ValueError('Number of columns should be at least 1.')
    # Pad out the list of alignments with itself
    alignments = alignments.split(',')
    alignments = alignments * math.ceil( colCount/len(alignments) )

    rows = [ input[i:i+colCount] for i in range(0, len(input), colCount) ]
    # Pad out the last row with empty strings
    rows[-1] += [''] * (colCount - len(rows[-1]))

    colWidths = [ max(len(row[i]) for row in rows) for i in range(colCount) ]
    if colNames:
        colWidths = [ max(w, len(name)) for (w, name) in zip(colWidths, colNames) ]

    def pad(text, width, where, what=' '):
        if where == 'l': return text.ljust(width, what)
        if where == 'c': return text.center(width, what)
        if where == 'r': return text.rjust(width, what)

    rows = [ ' %s ' % ' │ '.join([ pad(row[i], colWidths[i], alignments[i]) for i in range(colCount) ]) for row in rows ]
    if colNames:
        rows = [ '_%s_' % '_│_'.join([ pad(colNames[i], colWidths[i], alignments[i], '_') for i in range(colCount) ]) ] + rows

    return [ ('```%s```' if code_block else '%s') % '\n'.join(rows) ]

    

#####################################################
#                  Pipes : LETTER                   #
#####################################################
_CATEGORY = 'LETTER'

@make_pipe({
    'p' : Sig(float, 0.4, 'Character swap probability'),
}, command=True)
@as_map
def vowelize_pipe(text, p):
    '''Randomly replaces vowels.'''
    return vowelize(text, p)


@make_pipe({
    'p' : Sig(float, 0.4, 'Character swap probability'),
}, command=True)
@as_map
def consonize_pipe(text, p):
    '''Randomly replaces consonants with funnier ones.'''
    return consonize(text, p)


@make_pipe({
    'p' : Sig(float, 0.2, 'Character swap probability'),
}, command=True)
@as_map
def letterize_pipe(text, p):
    '''Both vowelizes and consonizes.'''
    return letterize(text, p)


@make_pipe({
    'p' : Sig(float, 0.4, 'Character swap probability'),
}, command=True)
@as_map
def letterize2_pipe(text, p):
    '''Letterize, but smarter™.'''
    return letterize2(text, p)


@make_pipe({
    'to' : Sig(str, None, 'Which conversion should be used.', options=converters.keys()),
}, command=True)
@as_map
@util.format_doc(convs=', '.join([c for c in converters]))
def convert_pipe(text, to):
    '''\
    Convert text using a variety of settings.

    Valid conversions: {convs}
    '''
    return converters[to.lower()](text)


#####################################################
#                  Pipes : LANGUAGE                 #
#####################################################
_CATEGORY = 'LANGUAGE'

# Wrap the API in a LRU cache
_datamuse = lru_cache()(datamuse_api.words)

@make_pipe({})
def split_sentences_pipe(input):
    ''' Splits a piece of text into individual sentences using the Natural Language Toolkit (NLTK). '''
    return [sent for line in input for sent in nltk.sent_tokenize(line)]


@make_pipe({
    'min': Sig(int, 0, 'Upper limit on minimum distance (e.g. 1 to never get the same word).'),
    'file': Sig(str, None, 'The name of the file to be matched from. >files for a list of files')
}, command=True)
@as_map
def nearest_pipe(text, min, file):
    '''Replaces text with the nearest item (by edit distance) from the given file.'''
    # TODO? MORE FILE LOGIC EQUIVALENT TO {TXT} SOURCE
    if file not in uploads:
        raise KeyError('No file "%s" loaded! Check >files for a list of files.' % file)
    file = uploads[file]
    return min_dist(text, min, file.get())


@make_pipe({}, command=True)
@word_map
def rhyme_pipe(word):
    '''
    Replaces words with random (nearly) rhyming words.
    Thanks to datamuse.com
    '''
    res = _datamuse(rel_rhy=word, max=10) or _datamuse(rel_nry=word, max=10)
    # if not res:
    #     res = _datamuse(arhy=1, max=5, sl=word)
    if res:
        return random.choice(res)['word']
    else:
        return word


@make_pipe({}, command=True)
@word_map
def homophone_pipe(word):
    '''
    Replaces words with random homophones.
    Thanks to datamuse.com
    '''
    res = _datamuse(rel_hom=word, max=5)
    if res:
        return random.choice(res)['word']
    else:
        return word


@make_pipe({}, command=True)
@word_map
def synonym_pipe(word):
    '''
    Replaces words with random antonyms.
    Thanks to datamuse.com
    '''
    res = _datamuse(rel_syn=word, max=5)
    if res:
        return random.choice(res)['word']
    else:
        return word


@make_pipe({}, command=True)
@word_map
def antonym_pipe(word):
    '''
    Replaces words with random antonyms.
    Thanks to datamuse.com
    '''
    res = _datamuse(rel_ant=word, max=5)
    if res:
        return random.choice(res)['word']
    else:
        return word


@make_pipe({}, command=True)
@word_map
def part_pipe(word):
    '''
    Replaces words with something it is considered "a part of", inverse of comprises pipe.
    Thanks to datamuse.com
    '''
    res = _datamuse(rel_par=word, max=5)
    if res:
        return random.choice(res)['word']
    else:
        return word


@make_pipe({}, command=True)
@word_map
def comprises_pipe(word):
    '''
    Replaces words with things considered "its parts", inverse of "part" pipe.
    Thanks to datamuse.com
    '''
    res = _datamuse(rel_com=word, max=10)
    if res:
        return random.choice(res)['word']
    else:
        return word


try:
    translate_client = translate.Client()
    # Wrap the API call in a LRU cache!
    _translate = lambda *a, **k : translate_client.translate(*a, **k, format_='text')
    _translate = lru_cache()(_translate)
except Exception as e:
    print(e)
    print('Failed to load google cloud translate services, translate will be unavailable!')
    _translate = None

# Retreived once using translate_client.get_languages()
translate_languages = '''af sq am ar hy az eu be bn bs bg ca ceb ny zh zh-TW co
hr cs da nl en eo et tl fi fr fy gl ka de el gu ht ha haw iw hi hmn hu is ig id
ga it ja jw kn kk km ko ku ky lo la lv lt lb mk mg ms ml mt mi mr mn my ne no ps
fa pl pt pa ro ru sm gd sr st sn sd si sk sl so es su sw sv tg ta te th tr uk ur
uz vi cy xh yi yo zu'''.split()

@make_pipe({
    'from': Sig(str, 'auto', 'The language code to translate from, "auto" to automatically detect the language.', options=translate_languages + ['auto']),
    'to' : Sig(str, 'en', 'The language code to translate to, "random" for a random language.', options=translate_languages + ['random']),
}, command=True)
@as_map
@util.format_doc(langs=' '.join(c for c in translate_languages))
def translate_pipe(text, to, **argc):
    '''
    Translates the input using the Google Cloud Translate API.
    The list of languages can be browsed at https://cloud.google.com/translate/docs/languages
    '''
    if _translate is None: return text
    if text.strip() == '': return text

    fro = argc['from'].lower() # because "from" is a keyword
    to = to.lower()
    if fro == 'auto': fro = ''
    if to == 'random': to = choose(translate_languages)

    result = _translate(text, source_language=fro, target_language=to)

    return result['translatedText']


#####################################################
#                  Pipes : ENCODING                 #
#####################################################
_CATEGORY = 'ENCODING'

@make_pipe({}, command=True)
@as_map
def demoji_pipe(text):
    '''Replaces emoji in text with their official names.'''
    out = []
    for c in text:
        if c in emoji.UNICODE_EMOJI:
            try:
                out.append( unicodedata2.name(c) + ' ' )
            except:
                out.append( '(UNKNOWN)' )
        else:
            out.append( c )
    return ''.join(out)


@make_pipe({}, command=True)
@as_map
def unicode_pipe(text):
    '''Replaces unicode characters with their official names.'''
    out = []
    for c in text:
        try: out.append(unicodedata2.name(c))
        except: out.append('UNKNOWN CHARACTER (%s)' % c)
    return ', '.join(out)


@make_pipe({
    'by': Sig(int, 13, 'The number of places to rotate the letters by.', lambda x: x in translate_languages or x == 'auto'),
}, command=True)
@as_map
def rot_pipe(text, by):
    '''Applies a Caeserian cypher.'''
    if by % 26 == 0: return text
    out = []
    for c in text:
        o = ord(c)
        if 97 <= o <= 122: # lowercase
            c = chr( 97 + ( o - 97 + by ) % 26 )
        elif 65 <= o <= 90: # uppercase
            c = chr( 65 + ( o - 65 + by ) % 26 )
        out.append(c)
    return ''.join(out)
