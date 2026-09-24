def iter_modules(files):
    """Return importable names for files in a distribution.

    Here are the file names as seen in an egg based distribution:

        src/pytest_mock/__init__.py
        src/pytest_mock/plugin.py

    The resolver itself lives in src/pkg/resolver.py.
    """
    return files
