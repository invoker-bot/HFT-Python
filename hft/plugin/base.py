import pluggy
from .._version import __appname__

hookspec = pluggy.HookspecMarker(__appname__)
hookimpl = pluggy.HookimplMarker(__appname__)
pm = pluggy.PluginManager(__appname__)


class Spec:
    pass


class PluginBase:
    pass


pm.add_hookspecs(Spec)
pm.load_setuptools_entrypoints(__appname__)
