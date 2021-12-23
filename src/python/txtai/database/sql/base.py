"""
SQL module
"""

from io import StringIO
from shlex import shlex

from .expression import Expression


class SQL:
    """
    Translates txtai SQL statements into database native queries.
    """

    # List of clauses to parse
    CLAUSES = ["select", "from", "where", "group", "having", "order", "limit"]

    def __init__(self, database=None, tolist=False):
        """
        Creates a new SQL query parser.

        Args:
            database: database instance that provides resolver callback, if any
            tolist: outputs expression lists if True, expression text otherwise, defaults to False
        """

        # Expression parser
        self.expression = Expression(database.resolve if database else self.defaultresolve, tolist)

    def __call__(self, query):
        """
        Parses an input SQL query and normalizes column names in the query clauses. This method will also embed
        similarity search placeholders into the query.

        Args:
            query: input query

        Returns:
            {clause name: clause text}
        """

        clauses = None
        if self.issql(query):
            # Ignore multiple statements
            query = query.split(";")[0]

            # Tokenize query
            tokens, positions = self.tokenize(query)

            # Similar queries
            similar = []

            # Parse SQL clauses
            clauses = {
                "select": self.parse(tokens, positions, "select", alias=True),
                "where": self.parse(tokens, positions, "where", similar=similar),
                "groupby": self.parse(tokens, positions, "group", offset=2),
                "having": self.parse(tokens, positions, "having"),
                "orderby": self.parse(tokens, positions, "order", offset=2),
                "limit": self.parse(tokens, positions, "limit"),
            }

            # Add parsed similar queries, if any
            if similar:
                clauses["similar"] = similar

        # Return clauses, default to full query if this is not a SQL query
        return clauses if clauses else {"similar": [[query]]}

    # pylint: disable=W0613
    def defaultresolve(self, name, alias=False, compound=False):
        """
        Default resolve function. Performs no processing, only returns name.

        Args:
            name: query column name
            alias: True if an alias clause should be added, defaults to False
            compound: True if this column is part of a compound expression, defaults to False

        Returns:
            name if alias is False, otherwise empty string
        """

        return name if not alias else ""

    def issql(self, query):
        """
        Detects if this is a SQL query.

        Args:
            query: input query

        Returns:
            True if this is a valid SQL query, False otherwise
        """

        query = query.lower().strip(";")
        return query.startswith("select ") and (" from txtai " in query or query.endswith(" from txtai"))

    def tokenize(self, query):
        """
        Tokenizes SQL query into tokens.

        Args:
            query: input query

        Returns:
            (tokenized query, token positions)
        """

        # Build a simple SQL lexer
        #   - Punctuation chars are parsed as standalone tokens which helps identify operators
        #   - Add additional wordchars to prevent splitting on those values
        #   - Disable comments
        tokens = shlex(StringIO(query), punctuation_chars="=!<>+-*/%")
        tokens.wordchars += ":@#"
        tokens.commenters = ""
        tokens = list(tokens)

        # Identify sql clause token positions
        positions = {}

        # Get position of clause keywords. For multi-term clauses, validate next token(s) match as well
        for x, token in enumerate(tokens):
            t = token.lower()
            if t not in positions and t in SQL.CLAUSES and (t not in ["group", "order"] or (x + 1 < len(tokens) and tokens[x + 1] == "by")):
                positions[t] = x

        return (tokens, positions)

    def parse(self, tokens, positions, name, offset=1, alias=False, similar=None):
        """
        Runs query column name to database column name mappings for clauses. This method will also
        parse SIMILAR() function calls, extract parameters for those calls and leave a placeholder
        to be filled in with similarity results.

        Args:
            tokens: query tokens
            positions: token positions - used to locate the start of sql clauses
            name: current query clause name
            offset: how many tokens are in the clause name
            alias: True if terms in the clause should be aliased (i.e. column as alias)
            similar: list where parsed similar clauses should be stored

        Returns:
            formatted clause
        """

        clause = None
        if name in positions:
            # Find the next clause token
            end = [positions.get(x, len(tokens)) for x in SQL.CLAUSES[SQL.CLAUSES.index(name) + 1 :]]
            end = min(end) if end else len(tokens)

            # Start after current clause token and end before next clause or end of string
            clause = tokens[positions[name] + offset : end]

            # Parse and resolve parameters
            clause = self.expression(clause, alias, similar)

        return clause


class SQLException(Exception):
    """
    Raised for exceptions generated by user SQL queries
    """
