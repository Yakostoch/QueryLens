import re

from PySide6 import QtGui


KEYWORDS = set("""
SELECT FROM WHERE AS DISTINCT ALL JOIN INNER LEFT RIGHT FULL OUTER CROSS ON
GROUP BY HAVING ORDER ASC DESC LIMIT OFFSET FETCH FIRST NEXT ROWS ONLY
INSERT INTO VALUES UPDATE SET DELETE CREATE ALTER DROP TABLE VIEW INDEX
DATABASE SCHEMA IF EXISTS PRIMARY KEY FOREIGN REFERENCES UNIQUE CHECK DEFAULT
NOT NULL AND OR IN IS LIKE ILIKE BETWEEN EXISTS CASE WHEN THEN ELSE END
UNION INTERSECT EXCEPT WITH RECURSIVE OVER PARTITION WINDOW RETURNING
BEGIN COMMIT ROLLBACK TRANSACTION TRUNCATE EXPLAIN ANALYZE TRUE FALSE
INT INTEGER BIGINT SMALLINT DECIMAL NUMERIC FLOAT DOUBLE REAL BOOLEAN
CHAR VARCHAR TEXT DATE TIME TIMESTAMP INTERVAL JSON JSONB UUID CAST
""".split())

SYNTAX_COLORS = {
    "dark": dict(keyword="#c4a7ff", string="#a4d69c", number="#f2bd83",
                 comment="#8496ad", function="#82cfff", identifier="#e6ce94",
                 operator="#a4b8d9"),
    "light": dict(keyword="#7040b0", string="#28733e", number="#a45316",
                  comment="#627189", function="#086d9b", identifier="#846019",
                  operator="#425c83"),
}

# Quotes and comments are consumed as a whole before any tokens inside them.
TOKEN = re.compile(
    r"--|/\*|['\"`\[]|"
    r"(?:\b\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\b|"
    r"[^\W\d][\w$]*|[+*/%=<>!|&^~:-]", re.UNICODE
)
QUOTE_STATES = {"'": 2, '"': 3, "`": 4, "]": 5}
STATE_QUOTES = {state: quote for quote, state in QUOTE_STATES.items()}


class SqlHighlighter(QtGui.QSyntaxHighlighter):
    """Basic SQL highlighting; states preserve multiline comments and quotes."""

    def __init__(self, document):
        super().__init__(document)
        self.set_theme("dark")

    def set_theme(self, theme):
        self.formats = {}
        for token, color in SYNTAX_COLORS[theme].items():
            style = QtGui.QTextCharFormat()
            style.setForeground(QtGui.QColor(color))
            if token == "keyword":
                style.setFontWeight(QtGui.QFont.Weight.DemiBold)
            if token == "comment":
                style.setFontItalic(True)
            self.formats[token] = style
        self.rehighlight()

    @staticmethod
    def quote_end(text, start, closer):
        while True:
            end = text.find(closer, start)
            if end == -1:
                return len(text), False
            if end + 1 < len(text) and text[end + 1] == closer:
                start = end + 2  # SQL escapes a quote by doubling it.
            else:
                return end + 1, True

    def highlightBlock(self, text):
        # Qt uses UTF-16 positions; Python counts an emoji as one character.
        offsets = [0]
        for char in text:
            offsets.append(offsets[-1] + (2 if ord(char) > 0xFFFF else 1))

        def paint(start, end, kind):
            self.setFormat(offsets[start], offsets[end] - offsets[start], self.formats[kind])

        self.setCurrentBlockState(0)
        position = 0
        previous = self.previousBlockState()
        if previous == 1:
            end = text.find("*/")
            position = len(text) if end == -1 else end + 2
            paint(0, position, "comment")
            if end == -1:
                self.setCurrentBlockState(1)
                return
        elif previous in STATE_QUOTES:
            closer = STATE_QUOTES[previous]
            position, closed = self.quote_end(text, 0, closer)
            paint(0, position, "string" if closer == "'" else "identifier")
            if not closed:
                self.setCurrentBlockState(previous)
                return

        while match := TOKEN.search(text, position):
            start, position = match.span()
            token = match.group()
            if token == "--":
                paint(start, len(text), "comment")
                return
            if token == "/*":
                end = text.find("*/", position)
                position = len(text) if end == -1 else end + 2
                paint(start, position, "comment")
                if end == -1:
                    self.setCurrentBlockState(1)
                    return
            elif token in ("'", '"', "`", "["):
                closer = "]" if token == "[" else token
                position, closed = self.quote_end(text, position, closer)
                paint(start, position, "string" if token == "'" else "identifier")
                if not closed:
                    self.setCurrentBlockState(QUOTE_STATES[closer])
                    return
            elif token[0].isdigit() or token[0] == ".":
                paint(start, position, "number")
            elif token.upper() in KEYWORDS:
                paint(start, position, "keyword")
            elif token[0].isalpha() or token[0] == "_":
                if text[position:].lstrip().startswith("("):
                    paint(start, position, "function")
            else:
                paint(start, position, "operator")
