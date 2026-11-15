def classFactory(iface):  # pylint: disable=invalid-name
    from .plugin import NenaRclPlugin
    return NenaRclPlugin(iface)
