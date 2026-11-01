from app.core.config import Settings


def _url(**kwargs):
    # Explicit credentials: importing LiteLLM (glm.py) copies .env into os.environ, which Settings would read.
    return Settings(_env_file=None, **{"user_name": "", "db_password": "", **kwargs}).sqlalchemy_database_url


def test_credentials_come_from_user_name_and_db_password():
    url = _url(database_url="postgresql://prod-db.example.com:5432/webblock?sslmode=require",
               user_name="aicode", db_password="p@ss:w/rd")
    assert url.drivername == "postgresql+psycopg"
    assert (url.username, url.password, url.host, url.port, url.database) == ("aicode", "p@ss:w/rd", "prod-db.example.com", 5432, "webblock")
    assert url.query == {"sslmode": "require"}


def test_they_override_credentials_in_the_url():
    url = _url(database_url="postgresql+psycopg://old:old@db/webblock", user_name="new", db_password="secret")
    assert (url.username, url.password) == ("new", "secret")


def test_a_full_database_url_is_used_as_is():
    url = _url(database_url="postgresql+psycopg://webblock:webblock@localhost:5433/webblock")
    assert url.render_as_string(hide_password=False) == "postgresql+psycopg://webblock:webblock@localhost:5433/webblock"


def test_jdbc_and_postgres_schemes_use_psycopg():
    for raw in ("jdbc:postgresql://db:5432/webblock", "postgres://db:5432/webblock"):
        assert _url(database_url=raw).drivername == "postgresql+psycopg"
