
_contracts = {}


class Contract:
    @classmethod
    def __init_subclass__(cls):
        # Apply checked decorator
        _contracts[cls.__name__] = cls

    @classmethod
    def check(cls, value):
        pass

    def __set__(self, instance, value):
        print("setting")
        self.check(value)
        instance.__dict__[self.name] = value

    def __set_name__(self, owner, name):
        print("setting name")
        self.name = name


class Typed(Contract):
    type = None

    @classmethod
    def check(cls, value):
        assert isinstance(value, cls.type), f'Expected {cls.type}'
        # does this super().check(value) is need?


class Integer(Typed):
    type = int

    @classmethod
    def check(cls, value):
        print("check Int")
        assert isinstance(value, int), 'Expect Int'
        super().check(value)


class Positive(Typed):
    @classmethod
    def check(cls, value):
        print("check Positive is ", value)
        assert value > 0, 'Must be > 0'
        super().check(value)


class PositiveInteger(Integer, Positive):
    # the super in Inter , and Postivie why
    # what if it super
    pass


from functools import wraps
from inspect import signature


def checked(func):
    sig = signature(func)   # can be bind here
    # ann = func.__annotations__
    ann = ChainMap(
        func.__annotations__,
        func.__globals__.get('__annotations__', {})
        # what the func :dx is __annotations__  in modu
    )
    print("ann is ", ann)
    @wraps(func)
    def wrapper(*args, **kwargs):
        bound = sig.bind(*args, **kwargs)
        for name, val in bound.arguments.items():
            if name in ann:
                print("check ", name)
                ann[name].check(val)
        return func(*args, **kwargs)
    return wrapper

from collections import ChainMap


class BaseMeta(type):
    @classmethod
    def __prepare__(cls, *args):
        return ChainMap({}, _contracts)

    def __new__(meta, name, bases, methods):
        methods = methods.maps[0]  # the origin dict
        return super().__new__(meta, name, bases, methods)


class Base(metaclass=BaseMeta):
    @classmethod
    def __init_subclass__(cls):
        # Apply checked decorator
        for name, val in cls.__dict__.items():
            if callable(val):
                setattr(cls, name, checked(val))

        for name, val in cls.__annotations__.items():
            contract = val()
            contract.__set_name__(cls, name)
            setattr(cls, name, contract)

    def __init__(self, *args):
        ann = self.__annotations__
        assert len(args) == len(ann), f"Expect"

        # 3.6 Order
        for name, val in zip(ann, args):
            setattr(self, name, val)


    def __repr__(self):
        args = ",".join(repr(getattr(self, name)) for name in self.__annotations__)
        return f'{type(self).__name__}({args})'


print("__contracts ", _contracts)
