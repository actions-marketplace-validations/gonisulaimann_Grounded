import sys
from typing import TYPE_CHECKING

from .utils import LazyModule, define_import_structure

if TYPE_CHECKING:
    from .models import *  # noqa
else:
    structure = define_import_structure(__file__)
    sys.modules[__name__] = LazyModule(__name__, structure, module_spec=__spec__)
