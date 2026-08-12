"""Imports every model module for its side effect of registering with `Base`.

Alembic's autogenerate only sees tables whose classes have been imported.  Any
new model module must be added here or its migration will silently come out
empty.
"""

from app.db.base import Base  # noqa: F401
from app.modules.catalog import models as catalog_models  # noqa: F401
from app.modules.jobs import models as jobs_models  # noqa: F401
from app.modules.notifications import models as notification_models  # noqa: F401
from app.modules.orders import models as orders_models  # noqa: F401
from app.modules.reviews import models as reviews_models  # noqa: F401
from app.modules.users import models as users_models  # noqa: F401
from app.modules.workers import models as workers_models  # noqa: F401

__all__ = ["Base"]
