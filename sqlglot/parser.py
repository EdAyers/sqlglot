from sqlglot.tokens import TokenType
import sqlglot.expressions as exp

class Parser:
    FUNCTIONS = {
        'AVG': lambda args: exp.Avg(this=args[0]),
        'CEIL': lambda args: exp.Ceil(this=args[0]),
        'COALESCE': lambda args: exp.Coalesce(expressions=args),
        'FIRST': lambda args: exp.First(this=args[0]),
        'FLOOR': lambda args: exp.Floor(this=args[0]),
        'LAST': lambda args: exp.Last(this=args[0]),
        'IF': lambda args: exp.If(condition=args[0], true=args[1], false=args[2]),
        'LN': lambda args: exp.LN(this=args[0]),
        'MAX': lambda args: exp.Max(this=args[0]),
        'MIN': lambda args: exp.Min(this=args[0]),
        'SUM': lambda args: exp.Sum(this=args[0]),
        'RANK': lambda args: exp.Rank(this=args[0]),
        'ROW_NUMBER': lambda args: exp.RowNumber(this=args[0]),
    }

    TYPE_TOKENS = {
        TokenType.BOOLEAN,
        TokenType.TINYINT,
        TokenType.SMALLINT,
        TokenType.INT,
        TokenType.BIGINT,
        TokenType.FLOAT,
        TokenType.DOUBLE,
        TokenType.DECIMAL,
        TokenType.CHAR,
        TokenType.VARCHAR,
        TokenType.TEXT,
        TokenType.BINARY,
        TokenType.JSON,
    }

    COLUMN_TOKENS = {
        TokenType.VAR,
        TokenType.IDENTIFIER,
        TokenType.STAR,
    }

    def __init__(self, **opts):
        self.functions = {**self.FUNCTIONS, **opts.get('functions', {})}
        self.reset()

    def reset(self):
        self._tokens = []
        self._chunks = [[]]
        self._index = 0

    def parse(self, raw_tokens):
        self.reset()

        for token in raw_tokens:
            if token.token_type == TokenType.SEMICOLON:
                self._chunks.append([])
            self._chunks[-1].append(token)

        expressions = []

        for tokens in self._chunks:
            self._index = -1
            self._advance()
            self._tokens = tokens
            expressions.append(self._parse_statement())
            if self._index < len(self._tokens):
                raise ValueError(f"Invalid expression {self._curr}")

        return expressions

    def _advance(self):
        self._index += 1

    @property
    def _prev(self):
        return self._safe_get(self._index - 1)

    @property
    def _curr(self):
        return self._safe_get(self._index)

    @property
    def _next(self):
        return self._safe_get(self._index + 1)

    def _safe_get(self, index):
        try:
            return self._tokens[index]
        except IndexError:
            return None

    def _parse_statement(self):
        if not self._match(TokenType.WITH):
            return self._parse_select()

        expressions = self._parse_csv(self._parse_cte)
        return exp.CTE(this=self._parse_select(), expressions=expressions)

    def _parse_cte(self):
        if not self._match(TokenType.IDENTIFIER, TokenType.VAR):
            raise ValueError('Expected alias after WITH')

        alias = self._prev

        if not self._match(TokenType.ALIAS):
            raise ValueError('Expected AS after WITH')

        return exp.Alias(this=self._parse_table(), to=alias)

    def _parse_select(self):
        if not self._match(TokenType.SELECT):
            return None

        this = exp.Select(expressions=self._parse_csv(self._parse_expression))
        this = self._parse_from(this)
        this = self._parse_join(this)
        this = self._parse_where(this)
        this = self._parse_group(this)
        this = self._parse_having(this)
        this = self._parse_order(this)
        this = self._parse_union(this)

        return this

    def _parse_from(self, this):
        if not self._match(TokenType.FROM):
            return this

        return exp.From(this=self._parse_table(), expression=this)

    def _parse_join(self, this):
        side = None
        kind = None

        if self._match(TokenType.LEFT, TokenType.RIGHT, TokenType.FULL):
            side = self._prev

        if self._match(TokenType.INNER, TokenType.OUTER, TokenType.CROSS):
            kind = self._prev

        if self._match(TokenType.JOIN):
            on = None
            expression = self._parse_table()

            if self._match(TokenType.ON):
                on = self._parse_expression()

            return self._parse_join(exp.Join(this=expression, expression=this, side=side, kind=kind, on=on))

        return this

    def _parse_table(self):
        if self._match(TokenType.L_PAREN):
            nested = self._parse_select()

            if not self._match(TokenType.R_PAREN):
                raise ValueError('Expecting )')
            expression = nested
        else:
            db = None
            table = None

            if self._match(TokenType.VAR, TokenType.IDENTIFIER):
                table = self._prev

            if self._match(TokenType.DOT):
                db = table
                if not self._match(TokenType.VAR, TokenType.IDENTIFIER):
                    raise ValueError('Expected table name')
                table = self._prev

            expression = exp.Table(this=table, db=db)

        return self._parse_alias(expression)

    def _parse_where(self, this):
        if not self._match(TokenType.WHERE):
            return this
        return exp.Where(this=this, expression=self._parse_conjunction())

    def _parse_group(self, this):
        if not self._match(TokenType.GROUP):
            return this

        if not self._match(TokenType.BY):
            raise ValueError('Expecting BY')

        return exp.Group(this=this, expressions=self._parse_csv(self._parse_primary))

    def _parse_having(self, this):
        if not self._match(TokenType.HAVING):
            return this
        return exp.Having(this=this, expression=self._parse_conjunction())

    def _parse_order(self, this):
        if not self._match(TokenType.ORDER):
            return this

        if not self._match(TokenType.BY):
            raise ValueError('Expecting BY')

        return exp.Order(this=this, expressions=self._parse_csv(self._parse_primary), desc=self._match(TokenType.DESC))

    def _parse_union(self, this):
        if not self._match(TokenType.UNION):
            return this

        distinct = not self._match(TokenType.ALL)

        return exp.Union(this=this, expression=self._parse_select(), distinct=distinct)

    def _parse_expression(self):
        return self._parse_alias(self._parse_window(self._parse_conjunction()))

    def _parse_conjunction(self):
        return self._parse_tokens(self._parse_equality, exp.And, exp.Or)

    def _parse_equality(self):
        return self._parse_tokens(self._parse_comparison, exp.EQ, exp.NEQ, exp.Is)

    def _parse_comparison(self):
        return self._parse_tokens(self._parse_range, exp.GT, exp.GTE, exp.LT, exp.LTE)

    def _parse_range(self):
        this = self._parse_term()

        if self._match(TokenType.IN):
            if not self._match(TokenType.L_PAREN):
                raise ValueError('Expected ( after IN')
            expressions = self._parse_csv(self._parse_primary)
            if not self._match(TokenType.R_PAREN):
                raise ValueError('Expected ) after IN')
            return exp.In(this=this, expressions=expressions)

        if self._match(TokenType.BETWEEN):
            low = self._parse_primary()
            self._match(TokenType.AND)
            high = self._parse_primary()
            return exp.Between(this=this, low=low, high=high)

        return this

    def _parse_term(self):
        return self._parse_tokens(self._parse_factor, exp.Minus, exp.Plus)

    def _parse_factor(self):
        return self._parse_tokens(self._parse_unary, exp.Slash, exp.Star)

    def _parse_unary(self):
        if self._match(TokenType.NOT):
            return exp.Not(this=self._parse_unary())
        if self._match(TokenType.DASH):
            return exp.Neg(this=self._parse_unary())
        return self._parse_special()

    def _parse_special(self):
        if self._match(TokenType.CAST):
            return self._parse_cast()
        if self._match(TokenType.CASE):
            return self._parse_case()
        if self._match(TokenType.COUNT):
            return self._parse_count()
        return self._parse_primary()

    def _parse_case(self):
        ifs = []
        default = None

        while self._match(TokenType.WHEN):
            condition = self._parse_expression()
            self._match(TokenType.THEN)
            then = self._parse_expression()
            ifs.append(exp.If(condition=condition, true=then))

        if self._match(TokenType.ELSE):
            default = self._parse_expression()

        if not self._match(TokenType.END):
            raise ValueError('Expected END after CASE')

        return exp.Case(ifs=ifs, default=default)

    def _parse_count(self):
        if not self._match(TokenType.L_PAREN):
            raise ValueError("Expected ( after COUNT")

        distinct = self._match(TokenType.DISTINCT)
        this = self._parse_conjunction()

        if not self._match(TokenType.R_PAREN):
            raise ValueError("Expected ) after COUNT")

        return exp.Count(this=this, distinct=distinct)

    def _parse_cast(self):
        if not self._match(TokenType.L_PAREN):
            raise ValueError("Expected ( after CAST")

        this = self._parse_conjunction()

        if not self._match(TokenType.ALIAS):
            raise ValueError("Expected AS after CAST")

        if not self._match(*self.TYPE_TOKENS):
            raise ValueError("Expected type after CAST")

        to = self._prev

        if not self._match(TokenType.R_PAREN):
            raise ValueError("Expected ) after CAST")

        return exp.Cast(this=this, to=to)

    def _parse_primary(self):
        if self._match(TokenType.STRING, TokenType.NUMBER, TokenType.STAR, TokenType.NULL):
            return self._prev

        if self._match(TokenType.L_PAREN):
            this = self._parse_expression()

            if not self._match(TokenType.R_PAREN):
                raise ValueError('Expecting )')
            return exp.Paren(this=this)

        return self._parse_column()

    def _parse_column(self):
        if not self._match(TokenType.VAR, TokenType.IDENTIFIER):
            return None

        db = None
        table = None
        this = self._prev

        if self._match(TokenType.L_PAREN):
            if this.token_type == TokenType.IDENTIFIER:
                raise ValueError('Unexpected (')

            function = self.functions.get(this.text.upper())

            if not function:
                raise ValueError(f"Unrecognized function name {this}")
            function = function(self._parse_csv(self._parse_expression))
            if not self._match(TokenType.R_PAREN):
                raise ValueError(f"Expected ) after function {this}")
            return function

        if self._match(TokenType.DOT):
            table = this
            if not self._match(*self.COLUMN_TOKENS):
                raise ValueError('Expected column name')
            this = self._prev

            if self._match(TokenType.DOT):
                db = table
                table = this
                if not self._match(*self.COLUMN_TOKENS):
                    raise ValueError('Expected column name')
                this = self._prev

        return self._parse_brackets(exp.Column(this=this, db=db, table=table))

    def _parse_brackets(self, this):
        if not self._match(TokenType.L_BRACKET):
            return this

        bracket = exp.Bracket(this=this, expressions=self._parse_csv(self._parse_primary))

        if not self._match(TokenType.R_BRACKET):
            raise ValueError(f"Expected ] after {this}[")

        return bracket

    def _parse_window(self, this):
        if not self._match(TokenType.OVER):
            return this

        if not self._match(TokenType.L_PAREN):
            raise ValueError('Expecting ( after OVER')

        partition = None

        if self._match(TokenType.PARTITION):
            if not self._match(TokenType.BY):
                raise ValueError('Expecting BY after PARTITION')
            partition = self._parse_csv(self._parse_primary)

        order = self._parse_order(None)

        if not self._match(TokenType.R_PAREN):
            raise ValueError('Expecting )')

        return exp.Window(this=this, partition=partition, order=order)

    def _parse_alias(self, this):
        self._match(TokenType.ALIAS)

        if self._match(TokenType.IDENTIFIER, TokenType.VAR):
            return exp.Alias(this=this, to=self._prev)

        return this

    def _parse_csv(self, parse):
        items = [parse()]

        while self._match(TokenType.COMMA):
            items.append(parse())

        return items

    def _parse_tokens(self, parse, *expressions):
        this = parse()

        expressions = {expression.token_type: expression for expression in expressions}

        while self._match(*expressions):
            this = expressions[self._prev.token_type](this=this, expression=parse())

        return this

    def _match(self, *types):
        if not self._curr:
            return False

        for token_type in types:
            if self._curr.token_type == token_type:
                self._advance()
                return True

        return False
