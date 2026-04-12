def run() -> None:
    try:
        raise ValueError("boom")
    finally:
        print("finally ran")


def main() -> None:
    try:
        run()
    except ValueError as e:
        print("outer caught")
        print(str(e))


main()
