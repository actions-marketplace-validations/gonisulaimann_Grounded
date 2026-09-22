import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def client():
    return MagicMock()


@patch("pkg.mod.Thing", create=True)
def test_show(client, mock_thing):
    assert client
    assert mock_thing
