# pcc for Python — How-To

Short recipes for common tasks. Each is end-to-end tested under
`tests/py_corpus/phase[1-4]/`.

---

## 1. Run a typed function fast

Stay fully annotated to avoid the CPython fallback.

```python
def mandelbrot(cx: float, cy: float, n: int) -> int:
    x: float = 0.0
    y: float = 0.0
    i: int = 0
    while i < n:
        if x * x + y * y > 4.0:
            return i
        x2: float = x * x - y * y + cx
        y = 2.0 * x * y + cy
        x = x2
        i = i + 1
    return n


def main() -> None:
    print(mandelbrot(-0.5, 0.5, 100))


main()
```

---

## 2. Read a stdlib value

```python
import sys

def main() -> None:
    print(sys.platform)

main()
```

pcc auto-links libpython; any `import` triggers it.

---

## 3. Call a stdlib function

```python
import math

def main() -> None:
    print(math.pow(2.0, 10.0))
    print(math.sqrt(16.0))

main()
```

Scalar args (int / float / str) get marshalled to CPython
`PyObject *`; the result is tagged as a CPython value and
`print(...)` converts to pcc-native str.

---

## 4. Handle an exception

```python
def main() -> None:
    try:
        raise ValueError("bad input")
    except ValueError as e:
        print("caught")
        print(str(e))

main()
```

---

## 5. Define a class with properties

```python
class Temperature:
    def __init__(self, celsius: int) -> None:
        self._celsius = celsius

    @property
    def celsius(self) -> int:
        return self._celsius

    @celsius.setter
    def celsius(self, v: int) -> None:
        self._celsius = v


def main() -> None:
    t = Temperature(20)
    print(t.celsius)
    t.celsius = 30
    print(t.celsius)


main()
```

---

## 6. Overload arithmetic + comparison

```python
class Money:
    def __init__(self, amount: int) -> None:
        self.amount = amount

    def __add__(self, other: "Money") -> "Money":
        return Money(self.amount + other.amount)

    def __lt__(self, other: "Money") -> bool:
        return self.amount < other.amount


def main() -> None:
    a = Money(100)
    b = Money(50)
    c = a + b
    print(c.amount)
    print(a < b)


main()
```

---

## 7. Use `with open(...)` / any context manager

Works for CPython-backed context managers (happy-path exit; exception
propagation through `__exit__` is a pending gap).

```python
import io

def main() -> None:
    with io.StringIO() as buf:
        buf.write("hello")
        print(buf.getvalue())

main()
```

---

## 8. Iterate a CPython iterable

```python
import os

def main() -> None:
    for entry in os.listdir("."):
        print(entry)

main()
```

---

## 9. Module-level constants

```python
PI: float = 3.14159
MAX: int = 100
NAME: str = "pcc"


def main() -> None:
    print(PI)
    print(MAX)
    print(NAME)


main()
```

---

## 10. Emit LLVM IR for debugging

```bash
python -m pcc myfile.py --emit-llvm -o myfile.ll
cat myfile.ll | head -40
```

For benchmark + acceptance of the whole corpus:

```bash
python tests/py_corpus/run_pcc.py --bench
```
