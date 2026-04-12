import datetime


def main() -> None:
    now = datetime.datetime.now()
    year = now.year
    if year >= 2024:
        print("modern")
    else:
        print("ancient")


main()
