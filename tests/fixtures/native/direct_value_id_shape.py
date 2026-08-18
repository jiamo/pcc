class Value:
    def __init__(self, ref: str) -> None:
        self._ref = ref
        self._direct_value_id = -1


class Builder:
    def publish(self, value: Value) -> int:
        existing = value._direct_value_id
        if existing >= 0:
            return existing
        name = value._ref[1:]
        value_id = len(name)
        value._direct_value_id = value_id
        return value_id


builder = Builder()
value = Value("%slot")
print(builder.publish(value))
print(builder.publish(value))
