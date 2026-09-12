from datetime import date

from app.models import Recipe
from app.vault import markdown_to_recipe, recipe_to_markdown


def _sample_recipe() -> Recipe:
    return Recipe(
        title="Chicken Tikka Masala",
        tags=["cuisine/indian", "meal/dinner"],
        servings=4,
        prep_time="20m",
        cook_time="35m",
        total_time="55m",
        difficulty="medium",
        source="family recipe, transcribed 2026-09-11",
        created=date(2026, 9, 11),
        ai_filled=["cook_time", "difficulty"],
        ingredients=["500g chicken thigh, cubed", "200g plain yogurt"],
        steps=[
            "Marinate chicken in yogurt + spices, 2+ hours.",
            "Sear chicken in batches, set aside.",
        ],
        notes="Freezes well. Great with basmati rice or naan.",
    )


def test_round_trip():
    original = _sample_recipe()
    markdown = recipe_to_markdown(original)
    restored = markdown_to_recipe(markdown)
    assert restored == original


def test_round_trip_without_notes():
    original = _sample_recipe()
    original.notes = None
    markdown = recipe_to_markdown(original)
    restored = markdown_to_recipe(markdown)
    assert restored == original
