from pathlib import Path


ROOT = (Path(__file__).resolve().parents[1] / "src")
SCRIPT = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "app.css").read_text(encoding="utf-8")
DARK_CSS = (ROOT / "web" / "dark-theme.css").read_text(encoding="utf-8")


def _function_source(name: str, next_name: str) -> str:
    return SCRIPT.split(f"function {name}", 1)[1].split(f"function {next_name}", 1)[0]


def test_editor_uses_one_shared_single_flight_busy_controller():
    state = SCRIPT.split("const app =", 1)[1].split("};", 1)[0]
    helper = _function_source("syncWorldPlayerEditorBusy", "applyWorldPlayerEditorResponse")

    assert "worldPlayerEditorBusy: false" in state
    assert "if (app.worldPlayerEditorBusy) return null;" in helper
    assert 'node?.setAttribute("aria-busy", String(busy))' in helper
    assert 'dialog?.querySelectorAll("button,input,select")' in helper
    assert 'control.dataset.worldPlayerEditorWasDisabled = String(control.disabled)' in helper
    assert 'delete control.dataset.worldPlayerEditorWasDisabled' in helper
    assert "finally" in helper
    assert SCRIPT.count("runWorldPlayerEditorAction(") >= 12


def test_every_editor_write_family_runs_through_the_busy_controller():
    editor = SCRIPT.split("async function saveWorldPlayerLanguage", 1)[1].split(
        "function openClubDetail", 1
    )[0]
    for endpoint in (
        "/api/world-players/language",
        "/api/world-players/preferred-move",
        "/api/world-players/second-nationality",
        "/api/world-players/primary-nationality",
        "/api/world-players/second-nationality/remove",
        "/api/world-players/injury/remove",
        "/api/world-players/unhappiness/clear",
        "/api/world-players/injury/add",
        "/api/world-players/retirement",
        "/api/world-players/contract",
        "/api/world-players/names",
        "/api/world-players/edit",
    ):
        endpoint_at = editor.index(endpoint)
        assert "runWorldPlayerEditorAction(" in editor[max(0, endpoint_at - 180) : endpoint_at]

    main_save = _function_source("saveWorldPlayerEditor", "openClubDetail")
    assert 'querySelectorAll("button,input")' not in main_save


def test_editor_exposes_busy_status_and_keyboard_focus_without_small_text():
    assert (
        'id="world-player-editor-dialog" class="club-detail-dialog world-player-editor-dialog" '
        'aria-labelledby="world-player-editor-title"'
    ) in HTML
    assert 'aria-describedby="world-player-editor-subtitle world-player-editor-status"' in HTML
    assert 'id="world-player-editor-status" class="world-player-editor-status" role="status" aria-live="polite" hidden' in HTML
    assert 'id="world-player-editor-form" aria-busy="false"' in HTML
    assert 'id="world-player-editor-content" class="world-player-editor-content" aria-busy="false"' in HTML
    assert ".world-player-editor-status" in CSS
    assert "font-size:13px" in CSS
    assert ".world-player-editor-dialog button:focus-visible" in CSS
    assert ".world-player-editor-status > i { animation:none; }" in CSS
    assert ':root[data-theme="dark"] .world-player-editor-status' in DARK_CSS


def test_attribute_draft_panel_uses_its_own_hooks_and_previews_through_the_busy_controller():
    editor = _function_source("openWorldPlayerEditor", "openPlayerEditorFor")
    for hook in (
        "data-world-player-draft-style",
        "data-world-player-draft-ca",
        "data-world-player-draft-generate",
        "data-world-player-draft-status",
    ):
        assert hook in editor
    assert 'editor.options?.attribute_styles' in editor
    # The panel must not reuse the attribute hook, or collectWorldPlayerEdits
    # would treat the controls as attribute values.
    panel = editor.split("world-player-editor-draft", 1)[1].split("const nameSection", 1)[0]
    assert "data-world-player-attribute=" not in panel

    draft = _function_source("generateWorldPlayerAttributeDraft", "openWorldPlayerEditor")
    endpoint_at = draft.index("/api/world-players/attribute-draft")
    assert "runWorldPlayerEditorAction(" in draft[max(0, endpoint_at - 180): endpoint_at]
    assert "expected_raw:player.editor?.expected_raw?.attributes" in draft


def test_applying_a_draft_fills_inputs_and_never_rerenders_the_dialog():
    apply_source = _function_source("applyWorldPlayerAttributeDraft", "generateWorldPlayerAttributeDraft")

    assert 'content.querySelectorAll("[data-world-player-attribute]")' in apply_source
    assert "input.value = String(Number(row.after))" in apply_source
    assert 'content.querySelector(\'[data-world-player-field="ca"]\')' in apply_source
    assert 'input.dataset.draftChanged = "true"' in apply_source
    assert 'uiText("world.editor.draft_applied"' in apply_source
    assert 'uiText("world.editor.draft_missed"' in apply_source
    # A re-render would overwrite the values that were just filled in, because
    # the diff still compares against the snapshot taken when the dialog opened.
    for forbidden in ("openWorldPlayerEditor(", "applyWorldPlayerEditorResponse(", "openClubDetail("):
        assert forbidden not in apply_source


def test_attribute_draft_panel_keeps_text_readable_in_both_themes():
    assert ".world-player-editor-draft{" in CSS
    assert ".world-player-editor-draft-status" in CSS
    assert '[data-draft-changed="true"]' in CSS
    assert ':root[data-theme="dark"] .world-player-editor-draft select' in DARK_CSS
    assert ':root[data-theme="dark"] .world-player-editor-draft-status[data-hit="false"]' in DARK_CSS
    assert ':root[data-theme="dark"] .world-player-editor-field input[data-draft-changed="true"]' in DARK_CSS
