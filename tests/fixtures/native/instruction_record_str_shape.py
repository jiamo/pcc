class Record:
    def __init__(self, text: str, opname: str) -> None:
        self.text = text
        self.opname = opname

    def __str__(self) -> str:
        if self.text:
            return self.text
        return self.opname


print(str(Record("", "ret")))
print(str(Record("full instruction", "unused")))
