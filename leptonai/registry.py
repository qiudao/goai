from loguru import logger


class Registry:
    def __init__(self):
        self._map = {}

    def register(self, keys, value):
        try:
            _ = iter(keys)
        except TypeError:
            is_iterable = False
        else:
            is_iterable = True

        if isinstance(keys, str) or not is_iterable:
            keys = [keys]

        for key in keys:
            if key in self._map:
                logger.warning(
                    f'Overriding previously registered "{key}" value "{value}"'
                )
            self._map[key] = value

    def get(self, key):
        if key in self._map:
            return self._map[key]
        return None

    def get_all(self):
        return self._map.keys()
