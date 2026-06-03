"""使 PyMySQL 作为 MySQLdb 使用，便于在 Windows 上连接 MySQL。"""
try:
    import pymysql

    pymysql.install_as_MySQLdb()
except ImportError:
    pass
