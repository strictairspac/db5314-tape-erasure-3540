"""测试公共支撑：内存 SQLite 引擎与 workflow 模块的依赖替换。"""

import os
import unittest

os.environ.setdefault('AUTH_KEY', 'test-auth-key')
os.environ.setdefault('DB_DRIVER', 'mysql+pymysql')

from sqlalchemy import create_engine, event
from sqlalchemy.pool import StaticPool

from src.models.migrate import migrate
from src.services import workflow


class FakeCache:
    """测试环境没有 Redis；唤醒信号本来就可缺失，队列以数据库为准。"""

    def lpush(self, *args):
        return 1

    def ltrim(self, *args):
        return True

    def brpop(self, *args, **kwargs):
        return None

    def ping(self):
        return True


def make_engine():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False},
                           poolclass=StaticPool)
    event.listen(engine, 'connect',
                 lambda dbapi_conn, _: dbapi_conn.execute('PRAGMA foreign_keys = ON'))
    migrate(engine)
    return engine


class WorkflowTestCase(unittest.TestCase):
    def setUp(self):
        self.engine = make_engine()
        self._restores = [(workflow, 'engine', workflow.engine),
                          (workflow, 'cache', workflow.cache)]
        workflow.engine = self.engine
        workflow.cache = FakeCache()

    def tearDown(self):
        for module, name, value in self._restores:
            setattr(module, name, value)
        self.engine.dispose()
