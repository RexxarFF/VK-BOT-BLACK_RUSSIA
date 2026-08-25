import pytest

from app.errors import ValidationError
from app.validators import normalize_vk_reference, parse_points, validate_nickname


def test_vk_reference_formats():
    assert normalize_vk_reference('123456') == 123456
    assert normalize_vk_reference('id123456') == 123456
    assert normalize_vk_reference('[id123456|User]') == 123456
    assert normalize_vk_reference('https://vk.com/id123456') == 123456
    assert normalize_vk_reference('@some_user') == 'some_user'


def test_group_reference_is_rejected():
    with pytest.raises(ValidationError):
        normalize_vk_reference('https://vk.com/club123456')


def test_nickname_and_points():
    assert validate_nickname('Felix_Wraith') == 'Felix_Wraith'
    assert parse_points('+5') == 5
    assert parse_points('-2') == -2
    with pytest.raises(ValidationError):
        validate_nickname('Феликс')
