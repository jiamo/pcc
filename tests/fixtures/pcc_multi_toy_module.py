class ToyError(Exception):
    pass


def main() -> None:
    print(123)
    try:
        raise ToyError("ok")
    except Exception as exc:
        print(str(exc))


if __name__ == "__main__":
    main()
