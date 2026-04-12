import os


def main() -> None:
    lang = os.getenv("LANG_THAT_DOES_NOT_EXIST_12345", "default-val")
    print(lang)


main()
