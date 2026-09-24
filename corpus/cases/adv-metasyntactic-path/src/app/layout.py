"""Import resolution for src layouts.

`import mypkg.x` lives at `src/mypkg/x.py`; a moved module such as
`src/old/x.py` must not resolve through an unrelated `x.py`.
"""


def resolve(name):
    # The real loader lives in src/app/loader.py since the split.
    return name
