# stateMachine.py
#
# module to define .pystate import handler
#
# import imputil
import keyword
import sys
import os
import types
import importlib
try:
    import urllib.parse
    url_parse = urllib.parse.urlparse
except ImportError:
    print("import error, Python 2 not supported")
    raise
    import urllib
    url_parse = urllib.parse


DEBUG = False

from pyparsing import Word, Group, ZeroOrMore, alphas, \
    alphanums, ParserElement, ParseException, ParseSyntaxException, \
    Empty, LineEnd, OneOrMore, col, Keyword, pythonStyleComment, \
    StringEnd, traceParseAction

class InvalidTransitionException(Exception): pass

ident = Word(alphas + "_", alphanums + "_$")

def no_keywords_allowed(s, l, t):
    wd = t[0]
    return not keyword.iskeyword(wd)

ident.addCondition(no_keywords_allowed, message="cannot use a Python keyword for state or transition identifier")

stateTransition = ident("from_state") + "->" + ident("to_state")
stateMachine = (Keyword("statemachine") + ident("name") + ":"
                + OneOrMore(Group(stateTransition))("transitions"))

namedStateTransition = (ident("from_state")
                        + "-(" + ident("transition") + ")->"
                        + ident("to_state"))
namedStateMachine = (Keyword("statemachine") + ident("name") + ":"
                     + OneOrMore(Group(namedStateTransition))("transitions"))


def expand_state_definition(source, loc, tokens):
    indent = " " * (col(loc, source) - 1)
    statedef = []

    # build list of states
    states = set()
    fromTo = {}
    for tn in tokens.transitions:
        states.add(tn.from_state)
        states.add(tn.to_state)
        fromTo[tn.from_state] = tn.to_state

    # define base class for state classes
    baseStateClass = tokens.name
    statedef.extend([
        "class %s(object):" % baseStateClass,
        "    def __str__(self):",
        "        return self.__class__.__name__",
        "    @classmethod",
        "    def states(cls):",
        "        return list(cls.__subclasses__)",
        "    def next_state(self):",
        "        return self._next_state_class()",
    ])

    # define all state classes
    statedef.extend("class {}({}): pass".format(s, baseStateClass) for s in states)

    # define state->state transitions
    statedef.extend("{}._next_state_class = {}".format(s, fromTo[s]) for s in states if s in fromTo)

    return indent + ("\n" + indent).join(statedef) + "\n"

stateMachine.setParseAction(expand_state_definition)


def expand_named_state_definition(source, loc, tokens):
    indent = " " * (col(loc, source) - 1)
    statedef = []
    # build list of states and transitions
    states = set()
    transitions = set()

    baseStateClass = tokens.name

    fromTo = {}
    for tn in tokens.transitions:
        states.add(tn.from_state)
        states.add(tn.to_state)
        transitions.add(tn.transition)
        if tn.from_state in fromTo:
            fromTo[tn.from_state][tn.transition] = tn.to_state
        else:
            fromTo[tn.from_state] = {tn.transition: tn.to_state}

    # add entries for terminal states
    for s in states:
        if s not in fromTo:
            fromTo[s] = {}

    # define state transition class
    statedef.extend([
        "class %sTransition:" % baseStateClass,
        "    def __str__(self):",
        "        return self.transitionName",
    ])
    statedef.extend(
        "{} = {}Transition()".format(tn, baseStateClass)
        for tn in transitions)
    statedef.extend("{}.transitionName = '{}'".format(tn, tn)
                    for tn in transitions)

    # define base class for state classes
    excmsg = "'" + tokens.name + \
             '.%s does not support transition "%s"' \
             "'% (self, name)"
    statedef.extend([
        "class %s(object):" % baseStateClass,
        "    def __str__(self):",
        "        return self.__class__.__name__",
        "    def next_state(self, name):",
        "        try:",
        "            return self.tnmap[tn]()",
        "        except KeyError:",
        "            import statemachine",
        "            raise statemachine.InvalidTransitionException(%s)" % excmsg,
        "    def __getattr__(self, name):",
        "        import statemachine",
        "        raise statemachine.InvalidTransitionException(%s)" % excmsg,
    ])

    # define all state classes
    statedef.extend("class %s(%s): pass" % (s, baseStateClass)
                        for s in states)

    # define state transition maps and transition methods
    for s in states:
        trns = list(fromTo[s].items())
        statedef.append("%s.tnmap = {%s}" % (s, ", ".join("%s:%s" % tn for tn in trns)))
        statedef.extend("%s.%s = staticmethod(lambda: %s())" % (s, tn_, to_)
                            for tn_, to_ in trns)

    return indent + ("\n" + indent).join(statedef) + "\n"

namedStateMachine.setParseAction(expand_named_state_definition)


# ======================================================================
# NEW STUFF - Matt Anderson, 2009-11-26
# ======================================================================
class SuffixImporter(object):
    """An importer designed using the mechanism defined in :pep:`302`. I read
    the PEP, and also used Doug Hellmann's PyMOTW article `Modules and
    Imports`_, as a pattern.

    .. _`Modules and Imports`: http://www.doughellmann.com/PyMOTW/sys/imports.html

    Define a subclass that specifies a :attr:`suffix` attribute, and
    implements a :meth:`process_filedata` method. Then call the classmethod
    :meth:`register` on your class to actually install it in the appropriate
    places in :mod:`sys`. """

    scheme = 'suffix'
    suffix = None
    path_entry = None

    @classmethod
    def trigger_url(cls):
        if cls.suffix is None:
            raise ValueError('%s.suffix is not set' % cls.__name__)
        return 'suffix:%s' % cls.suffix

    @classmethod
    def register(cls):
        sys.path_hooks.append(cls)
        sys.path.append(cls.trigger_url())

    def __init__(self, path_entry):
        pr = url_parse(str(path_entry))
        if pr.scheme != self.scheme or pr.path != self.suffix:
            raise ImportError()
        self.path_entry = path_entry
        self._found = {}

    def checkpath_iter(self, fullname):
        for dirpath in sys.path:
            # if the value in sys.path_importer_cache is None, then this
            # path *should* be imported by the builtin mechanism, and the
            # entry is thus a path to a directory on the filesystem;
            # if it's not None, then some other importer is in charge, and
            # it probably isn't even a filesystem path
            finder = sys.path_importer_cache.get(dirpath)
            if isinstance(finder, (type(None), importlib.machinery.FileFinder)):
                checkpath = os.path.join(dirpath, '{}.{}'.format(fullname, self.suffix))
                yield checkpath

    def find_module(self, fullname, path=None):
        for checkpath in self.checkpath_iter(fullname):
            if os.path.isfile(checkpath):
                self._found[fullname] = checkpath
                return self
        return None

    def load_module(self, fullname):
        assert fullname in self._found
        if fullname in sys.modules:
            module = sys.modules[fullname]
        else:
            sys.modules[fullname] = module = types.ModuleType(fullname)
        data = None
        with open(self._found[fullname]) as f:
            data = f.read()

        module.__dict__.clear()
        module.__file__ = self._found[fullname]
        module.__name__ = fullname
        module.__loader__ = self
        self.process_filedata(module, data)
        return module

    def process_filedata(self, module, data):
        pass


class PystateImporter(SuffixImporter):
    suffix = 'pystate'

    def process_filedata(self, module, data):
        # MATT-NOTE: re-worked :func:`get_state_machine`

        # convert any statemachine expressions
        stateMachineExpr = (stateMachine | namedStateMachine).ignore(pythonStyleComment)
        generated_code = stateMachineExpr.transformString(data)

        if DEBUG: print(generated_code)

        # compile code object from generated code
        # (strip trailing spaces and tabs, compile doesn't like
        # dangling whitespace)
        COMPILE_MODE = 'exec'

        codeobj = compile(generated_code.rstrip(" \t"),
                          module.__file__,
                          COMPILE_MODE)

        exec(codeobj, module.__dict__)


PystateImporter.register()

if DEBUG:
    print("registered {!r} importer".format(PystateImporter.suffix))
