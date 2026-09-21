"""Statement boundaries for the editor (not a SQL parser or validator)."""

import re
from dataclasses import dataclass


DOLLAR_QUOTE = re.compile(r"\$(?:[A-Za-z_][A-Za-z_0-9]*)?\$")


@dataclass(frozen=True)
class Statement:
    start: int
    end: int
    terminated: bool = False


@dataclass
class SqlContext:
    text: str
    statements: list[Statement]
    protected: list[tuple[int, int, bool]]

    def current(self, position):
        # Whitespace between statements belongs to the following statement.
        for index, statement in enumerate(self.statements):
            if position < statement.end:
                return index, statement
        if self.statements:
            last = self.statements[-1]
            if not self.text[last.end:position].strip():
                return len(self.statements) - 1, last
        return None

    def allows_completion(self, position):
        return not any(start < position < end or (position == end and not closed)
                       for start, end, closed in self.protected)


def scan_sql(text):
    statements = []
    protected = []
    start = position = 0
    has_code = False
    length = len(text)

    def append_statement(end, terminated=False):
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if has_code and left < right:
            statements.append(Statement(left, right, terminated))

    while position < length:
        char = text[position]
        if text.startswith("--", position):
            end = text.find("\n", position)
            end = length if end == -1 else end
            protected.append((position, end, False))
            position = end
        elif text.startswith("/*", position):
            begin = position
            position += 2
            depth = 1
            while position < length and depth:
                if text.startswith("/*", position):
                    depth += 1
                    position += 2
                elif text.startswith("*/", position):
                    depth -= 1
                    position += 2
                else:
                    position += 1
            protected.append((begin, position, depth == 0))
        elif char in "'\"`[":
            has_code = True
            begin = position
            closer = "]" if char == "[" else char
            # PostgreSQL E'...' strings also allow backslash escapes.
            escaped = (char == "'" and position > 0 and text[position - 1] in "eE"
                       and (position < 2 or not (text[position - 2].isalnum() or text[position - 2] == "_")))
            position += 1
            closed = False
            while position < length:
                if escaped and text[position] == "\\":
                    position = min(length, position + 2)
                elif text[position] == closer:
                    position += 1
                    if position < length and text[position] == closer:
                        position += 1
                    else:
                        closed = True
                        break
                else:
                    position += 1
            protected.append((begin, position, closed))
        elif char == "$" and (position == 0 or not (text[position - 1].isalnum() or text[position - 1] in "_$")) and (match := DOLLAR_QUOTE.match(text, position)):
            has_code = True
            end = text.find(match.group(), match.end())
            end_position = length if end == -1 else end + len(match.group())
            protected.append((position, end_position, end != -1))
            position = end_position
        elif char == ";":
            position += 1
            append_statement(position, terminated=True)
            start = position
            has_code = False
        else:
            if not char.isspace():
                has_code = True
            position += 1
    append_statement(length)
    return SqlContext(text, statements, protected)


def qt_position(text, position):
    """Convert a Python character index into a QTextCursor UTF-16 position."""
    return len(text[:position].encode("utf-16-le")) // 2


def python_position(text, position):
    return len(text.encode("utf-16-le")[:position * 2].decode("utf-16-le", errors="ignore"))
