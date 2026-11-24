"""Kleiner HTML-Parser ohne Fremdpaket: Baum aus Node, für Seiten, die über FlareSolverr gelesen werden."""
from html.parser import HTMLParser

VOID = {"img", "input", "link", "meta", "hr", "br", "source", "wbr", "area", "embed"}


class Node:
    def __init__(self, tag="", attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def walk(self):
        yield self
        for c in self.children:
            if isinstance(c, Node):
                yield from c.walk()

    def text(self, skip=lambda n: False) -> str:
        return "".join((c.text(skip) if not skip(c) else "") if isinstance(c, Node) else c for c in self.children)

    def cls(self, name: str) -> bool:
        return name in (self.attrs.get("class") or "").split()


class _Doc(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag == "br":
            self.stack[-1].children.append("\n")
        elif tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if self.stack[-1].tag not in ("script", "style"):
            self.stack[-1].children.append(data)


def parse(html: str) -> Node:
    return _Doc(html).root
