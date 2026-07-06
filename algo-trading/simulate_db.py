import os

_db_path = os.path.join(os.path.dirname(__file__), "db.py")
with open(_db_path, "r", encoding="utf-8") as _f:
    _code = _f.read()

_code = _code.replace('"users.db"', '"simulation.db"')
exec(_code, globals())
