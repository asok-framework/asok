from __future__ import annotations

import os

from .auth import AuthViewsMixin
from .crud import CRUDViewsMixin
from .helpers import HelperViewsMixin
from .media import MediaViewsMixin

_PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ViewsMixin(HelperViewsMixin, AuthViewsMixin, CRUDViewsMixin, MediaViewsMixin):
    pass
