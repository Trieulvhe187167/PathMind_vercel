"""Personal knowledge branch: each paid user's own tree beside the hidden main tree."""

from .service import (  # noqa: F401
    BranchError,
    create_node,
    delete_node,
    get_or_create_branch,
    list_versions,
    move_node,
    restore_version,
    save_version,
    set_prerequisites,
    tree_of,
    update_node,
)

from . import overlay  # noqa: E402,F401
from . import path  # noqa: E402,F401
