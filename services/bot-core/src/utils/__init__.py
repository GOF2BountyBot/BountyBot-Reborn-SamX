import importlib
import pkgutil

# Automatically import all modules in this package.
# The ``compute`` sub-package is a DB-free leaf designed for process-pool
# workers; it must NOT be dragged into the bulk auto-import chain that loads
# executors (and their transitive ORM/httpx dependencies).
package_name = __name__

_SKIP_PACKAGES = {"compute"}

for _loader, module_name, _is_pkg in pkgutil.walk_packages(__path__):
    # Skip the DB-free leaf package and all its sub-modules.
    top_name = module_name.split(".")[0]
    if top_name in _SKIP_PACKAGES:
        continue
    full_module_name = f"{package_name}.{module_name}"
    importlib.import_module(full_module_name)

# Optionally, define __all__ for explicit export
__all__ = [name for _, name, _ in pkgutil.iter_modules(__path__)]
