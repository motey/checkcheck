"""Pure-function tests for ``checkcheckserver.item_text``.

The vectors are mirrored verbatim in the frontend's
``tests/unit/itemBulk.spec.ts``: the client sorts and de-duplicates
optimistically with the twin in ``utils/itemBulk.ts``, so both sides must agree.
"""

import random

from checkcheckserver.item_text import item_sort_key, normalize_item_text

SORTED_VECTOR = [
    "1 apple",
    "2 eggs",
    "10 eggs",
    "Äpfel",
    "apple",
    "Apple pie",
    "banana",
    "Item 2",
    "item 10",
    "Zucchini",
    "",
]


def test_item_sort_key_vector():
    shuffled = SORTED_VECTOR[:]
    random.Random(4).shuffle(shuffled)
    assert sorted(shuffled, key=item_sort_key) == SORTED_VECTOR


def test_item_sort_key_ignores_case_whitespace_and_leading_zeros():
    assert item_sort_key("  Milk   shake ") == item_sort_key("milk shake")
    assert item_sort_key("007") == item_sort_key("7")
    assert item_sort_key("Crème") == item_sort_key("creme")


def test_normalize_item_text_matches_suggestion_rule():
    assert normalize_item_text("  Whole \t Milk ") == "whole milk"
    assert normalize_item_text(None) == ""
    # Accents are NOT folded for duplicates (same as the item suggestions).
    assert normalize_item_text("Crème") != normalize_item_text("creme")
