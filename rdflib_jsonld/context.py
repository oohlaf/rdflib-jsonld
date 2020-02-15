# -*- coding: utf-8 -*-
"""
Implementation of the JSON-LD Context structure. See:

    http://json-ld.org/

"""
from collections import namedtuple
from rdflib.namespace import RDF

from ._compat import basestring, unicode
from .keys import (BASE, CONTAINER, CONTEXT, GRAPH, ID, INCLUDED, INDEX, JSON,
        LANG, LIST, NEST, NONE, PREFIX, PROPAGATE, REV, SET, TYPE, VALUE,
        VERSION, VOCAB)
from . import errors
from .util import source_to_json, urljoin, urlsplit, split_iri, norm_url


NODE_KEYS = set([GRAPH, ID, INCLUDED, JSON, LIST, NEST, NONE, REV, SET, TYPE,
                 VALUE, LANG])

class Defined(int): pass
UNDEF = Defined(0)

# From <https://tools.ietf.org/html/rfc3986#section-2.2>
URI_GEN_DELIMS = (':', '/', '?', '#', '[', ']', '@')


class Context(object):

    def __init__(self, source=None, base=None, version=None):
        self.version = version or 1.0
        self.language = None
        self.vocab = None
        self.base = base
        self.doc_base = base
        self.terms = {}
        # _alias maps NODE_KEY to list of aliases
        self._alias = {}
        self._lookup = {}
        self._prefixes = {}
        self.active = False
        self.parent = None
        self.propagate = True
        if source:
            self.load(source)

    @property
    def base(self):
        return self._base

    @base.setter
    def base(self, base):
        if base:
            hash_index = base.find('#')
            if hash_index > -1:
                base = base[0:hash_index]
        self._base = self.resolve_iri(base) if (
                hasattr(self, '_base') and base is not None) else base
        self._basedomain = '%s://%s' % urlsplit(base)[0:2] if base else None

    def subcontext(self, source, propagate=True):
        # IMPROVE: to optimize, implement SubContext with parent fallback support
        parent = self.parent if self.propagate is False else self
        ctx = Context(version=parent.version)
        ctx.propagate = propagate
        ctx.parent = parent
        ctx.language = parent.language
        ctx.vocab = parent.vocab
        ctx.base = parent.base
        ctx.doc_base = parent.doc_base
        ctx._alias = parent._alias.copy()
        ctx.terms = parent.terms.copy()
        ctx._lookup = parent._lookup.copy()
        ctx._prefixes = parent._prefixes.copy()
        ctx.load(source)
        return ctx

    def get_context_for(self, key, node):
        if self.version >= 1.1:
            context = None
            propagate = True

            rtype = self.get_type(node) if isinstance(node, dict) else None
            if not isinstance(rtype, list):
                rtype = [rtype] if rtype else []

            for rt in sorted(rtype):
                typeterm = self.terms.get(rt)
                if typeterm:
                    break
            else:
                typeterm = None

            if typeterm and typeterm.context:
                context = typeterm.context
                propagate = False
            else:
                term = self.terms.get(key)
                if term and term.context:
                    context = term.context

            if context:
                return self.subcontext(context, propagate=propagate)

        return self.parent if self.propagate is False else self

    def get_id(self, obj):
        return self._get(obj, ID)

    def get_type(self, obj):
        return self._get(obj, TYPE)

    def get_language(self, obj):
        return self._get(obj, LANG)

    def get_value(self, obj):
        return self._get(obj, VALUE)

    def get_graph(self, obj):
        return self._get(obj, GRAPH)

    def get_list(self, obj):
        return self._get(obj, LIST)

    def get_set(self, obj):
        return self._get(obj, SET)

    def get_rev(self, obj):
        return self._get(obj, REV)

    def _get(self, obj, key):
        for alias in self._alias.get(key, []):
            if alias in obj:
                return obj.get(alias)
        return obj.get(key)

    def get_key(self, key):
        for alias in self.get_keys(key):
            return alias

    def get_keys(self, key):
        if key in self._alias:
            for alias in self._alias[key]:
                yield alias
        yield key

    lang_key = property(lambda self: self.get_key(LANG))
    id_key = property(lambda self: self.get_key(ID))
    type_key = property(lambda self: self.get_key(TYPE))
    value_key = property(lambda self: self.get_key(VALUE))
    list_key = property(lambda self: self.get_key(LIST))
    rev_key = property(lambda self: self.get_key(REV))
    graph_key = property(lambda self: self.get_key(GRAPH))

    def add_term(self, name, idref, coercion=UNDEF, container=UNDEF, index=None,
            language=UNDEF, reverse=False, context=None, prefix=None):
        if self.version < 1.1 or prefix is None:
            prefix = isinstance(idref, str) and idref.endswith(URI_GEN_DELIMS)

        if isinstance(container, (list, set, tuple)):
            container = set(container)
        else:
            container = set([container])

        term = Term(idref, name, coercion, container, index, language, reverse,
                context, prefix)

        self.terms[name] = term

        for container_key in (LIST, LANG, SET): #, INDEX, ID, GRAPH):
            if container_key in container:
                break
        else:
            container_key = UNDEF

        self._lookup[(idref, coercion or language, container_key, reverse)] = term

        if term.prefix is True:
            self._prefixes[idref] = name

    def find_term(self, idref, coercion=None, container=UNDEF,
            language=None, reverse=False):
        lu = self._lookup

        if coercion is None:
            coercion = language

        if coercion is not UNDEF and container:
            found = lu.get((idref, coercion, container, reverse))
            if found: return found

        if coercion is not UNDEF:
            found = lu.get((idref, coercion, UNDEF, reverse))
            if found: return found

        if container:
            found = lu.get((idref, coercion, container, reverse))
            if found: return found
        elif language:
            found = lu.get((idref, UNDEF, LANG, reverse))
            if found: return found
        else:
            found = lu.get((idref, coercion or UNDEF, SET, reverse))
            if found: return found

        return lu.get((idref, UNDEF, UNDEF, reverse))

    def resolve(self, curie_or_iri):
        iri = self.expand(curie_or_iri, False)
        if self.isblank(iri):
            return iri
        return self.resolve_iri(iri)

    def resolve_iri(self, iri):
        return norm_url(self._base, iri)

    def isblank(self, ref):
        return ref.startswith('_:')

    def expand(self, term_curie_or_iri, use_vocab=True):
        if not isinstance(term_curie_or_iri, unicode):
            return term_curie_or_iri

        if use_vocab:
            term = self.terms.get(term_curie_or_iri)
            if term:
                return term.id

        is_term, pfx, local = self._prep_expand(term_curie_or_iri)
        if pfx == '_':
            return term_curie_or_iri

        if pfx is not None:
            ns = self.terms.get(pfx)
            if ns and ns.prefix and ns.id:
                return ns.id + local
        elif is_term and use_vocab:
            if self.vocab:
                return self.vocab + term_curie_or_iri
            return None

        return self.resolve_iri(term_curie_or_iri)

    def shrink_iri(self, iri):
        ns, name = split_iri(unicode(iri))
        pfx = self._prefixes.get(ns)
        if pfx:
            return u":".join((pfx, name))
        elif self._base:
            if unicode(iri) == self._base:
                return ""
            elif iri.startswith(self._basedomain):
                    return iri[len(self._basedomain):]
        return iri

    def to_symbol(self, iri):
        iri = unicode(iri)
        term = self.find_term(iri)
        if term:
            return term.name
        ns, name = split_iri(iri)
        if ns == self.vocab:
            return name
        pfx = self._prefixes.get(ns)
        if pfx:
            return u":".join((pfx, name))
        return iri

    def load(self, source, base=None):
        self.active = True
        sources = []
        source = source if isinstance(source, list) else [source]
        self._prep_sources(base, source, sources)
        for source_url, source in sources:
            self._read_source(source, source_url)

    def _prep_sources(self, base, inputs, sources, referenced_contexts=None,
            in_source_url=None):
        referenced_contexts = referenced_contexts or set()
        for source in inputs:
            if source is None:
                continue
            if isinstance(source, basestring):
                source_url = urljoin(base, source)
                if source_url in referenced_contexts:
                    raise errors.RECURSIVE_CONTEXT_INCLUSION
                referenced_contexts.add(source_url)
                source = source_to_json(source_url)
                if source is None:
                    continue
                if CONTEXT not in source:
                    raise errors.INVALID_REMOTE_CONTEXT
            else:
                source_url = in_source_url

            if isinstance(source, dict):
                if CONTEXT in source:
                    source = source[CONTEXT]
                    source = source if isinstance(source, list) else [source]
            if isinstance(source, list):
                self._prep_sources(base, source, sources, referenced_contexts, source_url)
            else:
                sources.append((source_url, source))

    def _read_source(self, source, source_url=None):
        self.vocab = source.get(VOCAB, self.vocab)
        self.version = source.get(VERSION, self.version)
        for key, value in source.items():
            if key in {VOCAB, VERSION}:
                continue
            elif key == PROPAGATE and isinstance(value, bool):
                self.propagate = value
            elif key == LANG:
                self.language = value
            elif key == BASE:
                if source_url:
                    continue
                self.base = value
            else:
                self._read_term(source, key, value)

    def _read_term(self, source, name, dfn):
        idref = None
        if isinstance(dfn, dict):
            #term = self._create_term(source, key, value)
            rev = dfn.get(REV)

            idref = rev or dfn.get(ID, UNDEF)
            if idref == TYPE:
                idref = unicode(RDF.type)
            elif idref is not UNDEF:
                idref = self._rec_expand(source, idref)
            elif ':' in name:
                idref = self._rec_expand(source, name)
            elif self.vocab:
                idref = self.vocab + name

            coercion = dfn.get(TYPE, UNDEF)
            if coercion and coercion not in (ID, TYPE, VOCAB):
                coercion = self._rec_expand(source, coercion)

            context = dfn.get(CONTEXT)

            self.add_term(name, idref, coercion,
                    dfn.get(CONTAINER, UNDEF), dfn.get(INDEX, UNDEF),
                    dfn.get(LANG, UNDEF), bool(rev),
                    context, dfn.get(PREFIX))
        else:
            if isinstance(dfn, unicode):
                idref = self._rec_expand(source, dfn)
            self.add_term(name, idref)

        if idref in NODE_KEYS:
            self._alias.setdefault(idref, []).append(name)

    def _rec_expand(self, source, expr, prev=None):
        if expr == prev or expr in NODE_KEYS:
            return expr

        is_term, pfx, nxt = self._prep_expand(expr)
        if pfx:
            iri = self._get_source_id(source, pfx)
            if iri is None:
                if pfx + ':' == self.vocab:
                    return expr
                else:
                    term = self.terms.get(pfx)
                    if term:
                        iri = term.id

            if iri is None:
                nxt = expr
            else:
                nxt = iri + nxt
        else:
            nxt = self._get_source_id(source, nxt) or nxt
            if ':' not in nxt and self.vocab:
                return self.vocab + nxt

        return self._rec_expand(source, nxt, expr)

    def _prep_expand(self, expr):
        if ':' not in expr:
            return True, None, expr
        pfx, local = expr.split(':', 1)
        if not local.startswith('//'):
            return False, pfx, local
        else:
            return False, None, expr

    def _get_source_id(self, source, key):
        # .. from source dict or if already defined
        term = source.get(key)
        if term is None:
            dfn = self.terms.get(key)
            if dfn:
                term = dfn.id
        elif isinstance(term, dict):
            term = term.get(ID)
        return term


Term = namedtuple('Term',
        'id, name, type, container, index, language, reverse, context, prefix')
Term.__new__.__defaults__ = (UNDEF, UNDEF, None, UNDEF, False, None, False)
