"""Reuse immutable result queries during one training pass only."""


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class FeatureReader:
    def __init__(self, connection):
        self.connection = connection
        self.cache = {}

    def execute(self, sql, params=()):
        # No writes go through this reader. Cache only the repeated source
        # queries, not the many cutoff-specific metric queries.
        if sql.lstrip().upper().startswith('SELECT') and 'julianday(' not in sql and ('sports_ai_fixtures' in sql or 'sports_ai_settings' in sql):
            key = (sql, tuple(params))
            if key not in self.cache:
                self.cache[key] = self.connection.execute(sql, params).fetchall()
            return Rows(self.cache[key])
        return self.connection.execute(sql, params)
