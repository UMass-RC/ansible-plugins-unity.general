# if user does not have beartype installed, just don't use it
try:
    from beartype import beartype as _beartype
    from beartype import BeartypeConf

    beartype = _beartype(conf=BeartypeConf(is_color=False))
except ImportError:

    def beartype(func):
        return func
