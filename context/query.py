from collections.abc import Iterable
from dataclasses import dataclass


@dataclass
class QueryAtom:
    tag: str
    negative: bool

    @classmethod
    def from_string(cls, atom: str):
        negative = atom.startswith('-')
        if negative:
            atom = atom[1:]
        return QueryAtom(tag=atom, negative=negative)

    def __str__(self):
        return f'{"-" if self.negative else ""}{self.tag}'


class Query:
    def __init__(self, query: str):
        self.atoms = [QueryAtom.from_string(atom) for atom in query.split()]

    def __str__(self):
        return ' '.join(str(atom) for atom in self.atoms)

    def mentioned_tags(self):
        return {atom.tag for atom in self.atoms if not atom.negative}

    @classmethod
    def get_queries(cls, subs: list[str]) -> list['Query']:
        return [Query(q) for q in subs]

    def check(self, tags: Iterable[str]):
        tags = set(tags)
        result = True
        for atom in self.atoms:
            if atom.negative:
                if atom.tag in tags:
                    return False
            else:
                if atom.tag not in tags:
                    result = False
                    break
        return result
