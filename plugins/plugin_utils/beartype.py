# if user does not have beartype installed, just don't use it
try:
    from beartype import beartype
except ImportError:

    def beartype(func):
        return func
