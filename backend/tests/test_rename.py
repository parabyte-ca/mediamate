"""ScreenMate → MediaMate: existing installs keep their qBittorrent category, and the
release-check URL follows the renamed repository. Runs once."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import Setting, WatchlistItem
from app.services import rename


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'r.db'}")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def _v(db, key):
    row = db.query(Setting).filter(Setting.key == key).first()
    return row.value if row else None


def test_existing_install_keeps_screenmate_category(db):
    db.add(Setting(key="onboarding_done", value="true"))
    db.add(Setting(key="app_source_url", value="https://github.com/parabyte-ca/screenmate.git"))
    db.commit()
    assert rename.carry_over(db)
    assert _v(db, "qbit_category") == "screenmate"
    assert _v(db, "app_source_url") == "https://github.com/parabyte-ca/mediamate"
    assert not rename.carry_over(db)                       # once only


def test_chosen_category_and_custom_url_are_left_alone(db):
    db.add(WatchlistItem(title="Stick", search_query="Stick", season=1, episode=1))
    db.add(Setting(key="qbit_category", value="tv-auto"))
    db.add(Setting(key="app_source_url", value="https://github.com/someone/fork"))
    db.commit()
    rename.carry_over(db)
    assert _v(db, "qbit_category") == "tv-auto"
    assert _v(db, "app_source_url") == "https://github.com/someone/fork"


def test_fresh_install_gets_mediamate_defaults(db):
    db.add(Setting(key="session_secret", value="x"))       # created at first start
    db.commit()
    rename.carry_over(db)
    assert _v(db, "qbit_category") is None                 # so the "mediamate" default applies
