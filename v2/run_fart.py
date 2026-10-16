"""PyInstaller entry point.

`fart/__main__.py` uses relative imports (`from .ui.main_window import
MainWindow`), which only work when it's reached via `python -m fart` or
`import fart.__main__` -- both give it proper package context. Pointing
PyInstaller directly at fart/__main__.py runs it as a bare top-level
script instead, with no parent package, so those relative imports fail
with "attempted relative import with no known parent package". Importing
it as `fart.__main__` here (which imports the `fart` package first) gives
it that context before calling main().
"""
from fart.__main__ import main

if __name__ == "__main__":
    main()
