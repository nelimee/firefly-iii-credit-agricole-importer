from pathlib import Path

HERE = Path(__file__).parent
SRC = HERE.parent
MAIN_DIRECTORY = SRC.parent

FILE_DIRECTORY = MAIN_DIRECTORY / "files"

CA_RULES = FILE_DIRECTORY / "ca-rules.ini"
