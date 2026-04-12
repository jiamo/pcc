class Animal:
    def sound(self) -> None:
        print("generic")


class Cat(Animal):
    def sound(self) -> None:
        print("meow")


def main() -> None:
    a = Animal()
    c = Cat()
    a.sound()
    c.sound()


main()
