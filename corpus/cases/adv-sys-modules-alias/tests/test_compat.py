from pkg.packages.json.decoder import JSONDecoder
from pkg.helpers.strings import slugify


def test_alias():
    assert JSONDecoder
    assert slugify
