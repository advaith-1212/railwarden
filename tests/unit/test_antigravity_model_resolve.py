from __future__ import annotations

from lfg.planning.antigravity import antigravity_model_slug, resolve_antigravity_model


def test_antigravity_model_slug_normalizes_display_names() -> None:
    assert (
        antigravity_model_slug("Gemini 3.5 Flash (Low)")
        == "gemini-3.5-flash-low"
    )


def test_resolve_antigravity_model_maps_slug_to_display_name() -> None:
    resolved = resolve_antigravity_model(
        "gemini-3.5-flash-low",
        available_models=(
            "Gemini 3.5 Flash (Low)",
            "Claude Opus 4.6 (Thinking)",
        ),
    )
    assert resolved == "Gemini 3.5 Flash (Low)"


def test_resolve_antigravity_model_preserves_display_name() -> None:
    assert (
        resolve_antigravity_model(
            "Claude Opus 4.6 (Thinking)",
            available_models=("Claude Opus 4.6 (Thinking)",),
        )
        == "Claude Opus 4.6 (Thinking)"
    )