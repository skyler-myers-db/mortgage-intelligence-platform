from math_utils import subtract


def test_subtract_positive_numbers() -> None:
    assert subtract(5, 3) == 2


def test_subtract_negative_numbers() -> None:
    assert subtract(-2, 3) == -5


def test_subtract_float_numbers() -> None:
    assert subtract(5.5, 2.0) == 3.5
