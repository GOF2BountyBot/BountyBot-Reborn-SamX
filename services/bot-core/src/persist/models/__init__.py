import importlib
import pkgutil
import sys

# Automatically import all modules in this package
package_name = __name__

for loader, module_name, is_pkg in pkgutil.walk_packages(__path__):
    full_module_name = f"{package_name}.{module_name}"
    importlib.import_module(full_module_name)

# Optionally, define __all__ for explicit export
__all__ = [name for _, name, _ in pkgutil.iter_modules(__path__)]