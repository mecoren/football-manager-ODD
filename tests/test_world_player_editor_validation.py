from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = (Path(__file__).resolve().parents[1] / "src")
WEB = ROOT / "web"
SCRIPT = (WEB / "app.js").read_text(encoding="utf-8")
HTML = (WEB / "index.html").read_text(encoding="utf-8")
CSS = (WEB / "app.css").read_text(encoding="utf-8")
DARK_CSS = (WEB / "dark-theme.css").read_text(encoding="utf-8")

VALIDATION_KEYS = (
    "world.editor.validation.required",
    "world.editor.validation.integer",
    "world.editor.validation.decimals",
    "world.editor.validation.range",
    "world.editor.validation.date_range",
    "world.editor.validation.name_length",
    "world.editor.validation.summary",
    "world.editor.validation.warn_ca_pa",
    "world.editor.validation.warn_asking_price",
)
PACK_LOCALES = ("zh-TW", "de-DE", "es-ES", "fr-FR", "ru-RU", "ja-JP", "pt-BR", "pt-PT")
ALL_LOCALES = ("en-GB", "zh-CN", "ko-KR", *PACK_LOCALES)
ENGLISH_FUNCTION_WORDS = re.compile(
    r"\b(?:the|this|that|these|those|with|without|before|after|while|when|from|into"
    r"|only|cannot|could|would|should|will|has|have|was|were|are|and)\b",
    re.IGNORECASE,
)
ENTRY_POINTS = (
    ("saveWorldPlayerEditor", "openClubDetail", "/api/world-players/edit"),
    ("saveWorldPlayerContract", "saveWorldPlayerNames", "/api/world-players/contract"),
    ("saveWorldPlayerNames", "openSettingsWithTab", "/api/world-players/names"),
    ("saveWorldPlayerLanguage", "saveWorldPlayerPreferredMoves", "/api/world-players/language"),
)


def _function_source(name: str, next_name: str) -> str:
    return SCRIPT.split(f"function {name}", 1)[1].split(f"function {next_name}", 1)[0]


def _copy_probe() -> dict[str, object]:
    script = r'''
const fs = require("fs"), vm = require("vm"), root = process.argv[1];
const keys = JSON.parse(process.argv[2]);
const html = fs.readFileSync(root + "/web/index.html", "utf8");
const sources = [...html.matchAll(/<script\s+src="([^"]+)"/g)].map((match) => match[1]);
const context = {
  window:{FMODDI18nModules:[], FMODDLocalePacks:{}},
  document:{documentElement:{dataset:{}}, querySelector:()=>null, dispatchEvent:()=>{}},
  NodeFilter:{SHOW_TEXT:4}, CustomEvent:class CustomEvent {},
};
vm.createContext(context);
for (const source of sources) {
  if (!source.startsWith("/i18n") || source === "/i18n.js") continue;
  vm.runInContext(fs.readFileSync(root + "/web" + source, "utf8"), context, {filename:source});
}
vm.runInContext(fs.readFileSync(root + "/web/i18n.js", "utf8"), context, {filename:"/i18n.js"});
const i18n = context.window.FMODDI18n;
const locales = ["en-GB", "zh-CN", "ko-KR", "zh-TW", "de-DE", "es-ES", "fr-FR", "ru-RU", "ja-JP", "pt-BR", "pt-PT"];
const templates = {};
for (const locale of locales) {
  i18n.setLocale(locale, {persist:false, root:null});
  templates[locale] = Object.fromEntries(keys.map((key) => [key, i18n.t(key)]));
}
const packs = Object.fromEntries(locales
  .filter((locale) => locale !== "en-GB" && locale !== "zh-CN" && locale !== "ko-KR")
  .map((locale) => [locale, context.window.FMODDLocalePacks[locale]?.messages || {}]));
console.log(JSON.stringify({templates, packs}));
'''
    result = subprocess.run(
        ["node", "-e", script, str(ROOT), json.dumps(list(VALIDATION_KEYS))],
        check=True, capture_output=True, text=True, encoding="utf-8",
    )
    return json.loads(result.stdout)


def _placeholders(template: str) -> list[str]:
    return sorted(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", template))


def test_editor_form_takes_over_native_validation() -> None:
    # Without novalidate the browser blocks submit on an out-of-range number and
    # none of the localised copy below can ever appear. The attribute also has to
    # stay after aria-busy so the older structural assertion keeps matching.
    assert '<form id="world-player-editor-form" aria-busy="false" novalidate>' in HTML
    assert 'id="world-player-editor-form" aria-busy="false"' in HTML


def test_validator_block_sits_between_the_editor_opener_and_the_edit_collector() -> None:
    block = SCRIPT.split("function openPlayerEditorFor", 1)[1].split(
        "function collectWorldPlayerEdits", 1,
    )[0]
    for name in (
        "worldPlayerEditorIssueLabel",
        "worldPlayerEditorInputDirty",
        "worldPlayerEditorFieldIssues",
        "worldPlayerEditorIssues",
        "worldPlayerEditorReportIssues",
        "worldPlayerEditorGate",
        "worldPlayerEditorIssueInput",
    ):
        assert f"function {name}(" in block
    # The block sits in the slice that pins Han literals to the hidden-attribute
    # names, so a stray Chinese comment here fails the editor copy contract.
    assert re.findall(r"[\u4e00-\u9fff]+", block) == []


def test_bounds_come_from_the_rendered_inputs_not_a_second_table() -> None:
    body = _function_source("worldPlayerEditorFieldIssues", "worldPlayerEditorIssues")
    assert "input.min" in body
    assert "input.max" in body
    assert 'getAttribute("step")' in body
    assert 'min="' not in body and 'max="' not in body
    # A validator that rewrites the value would silently save a number the user
    # never typed, so clamping is explicitly out.
    for forbidden in ("input.value = ", "Math.min(", "Math.max("):
        assert forbidden not in body


def test_every_save_entry_point_validates_before_it_confirms_or_sends() -> None:
    for name, next_name, endpoint in ENTRY_POINTS:
        body = _function_source(name, next_name)
        starts = [
            body.index(marker) for marker in
            ("worldPlayerEditorGate(", "worldPlayerEditorReportIssues(") if marker in body
        ]
        assert starts, name
        guard = min(starts)
        assert guard < body.index(endpoint), name
        assert guard < body.index("fmoddConfirm("), name


def test_renderer_marks_fields_and_only_blocks_on_errors() -> None:
    body = _function_source("worldPlayerEditorReportIssues", "worldPlayerEditorGate")
    assert 'setAttribute("aria-invalid", "true")' in body
    assert 'removeAttribute("aria-invalid")' in body
    assert 'role="alert"' in body
    assert ".focus()" in body
    assert "scrollIntoView" in body
    # Soft warnings share the block but must not stop the save.
    assert "return errors.length > 0;" in body


def test_typing_revalidates_the_same_family_without_stealing_focus() -> None:
    assert SCRIPT.count('addEventListener("input", worldPlayerEditorIssueInput)') == 1
    assert '$("#world-player-editor-content")?.addEventListener("input", worldPlayerEditorIssueInput)' in SCRIPT
    body = _function_source("worldPlayerEditorIssueInput", "collectWorldPlayerEdits")
    assert "block.dataset.worldPlayerEditorIssues" in body
    assert "{focus:false}" in body


def test_soft_warnings_cover_ca_pa_and_the_zero_price_sentinel() -> None:
    body = _function_source("worldPlayerEditorIssues", "worldPlayerEditorReportIssues")
    assert "warn_ca_pa" in body
    assert "warn_asking_price" in body
    assert "WORLD_PLAYER_EDITOR_ZERO_PRICE_TOTALS.includes" in body
    assert "const WORLD_PLAYER_EDITOR_ZERO_PRICE_TOTALS = [300000000, 4294967295];" in SCRIPT
    assert "const WORLD_PLAYER_EDITOR_DATE_YEARS = {min:1900, max:2200};" in SCRIPT


def test_only_edited_fields_are_validated() -> None:
    dirty = _function_source("worldPlayerEditorInputDirty", "worldPlayerEditorFieldIssues")
    assert "input.defaultValue" in dirty
    body = _function_source("worldPlayerEditorIssues", "worldPlayerEditorReportIssues")
    # An untouched box keeps whatever the save already holds, so only boxes the
    # user actually edited may raise an issue.
    assert body.index("worldPlayerEditorInputDirty(input)") < body.index("worldPlayerEditorFieldIssues(input)")


def test_editor_inputs_still_render_the_server_bounds() -> None:
    assert 'min="${min}" max="${max}" step="${step}"' in SCRIPT


def test_validation_copy_resolves_in_every_locale_with_matching_placeholders() -> None:
    probe = _copy_probe()
    templates = probe["templates"]
    english = templates["en-GB"]
    for key in VALIDATION_KEYS:
        assert _placeholders(english[key]), key
    for locale in ALL_LOCALES:
        for key in VALIDATION_KEYS:
            value = templates[locale][key]
            assert value and value != key, (locale, key)
            assert _placeholders(value) == _placeholders(english[key]), (locale, key)
    packs = probe["packs"]
    for locale in PACK_LOCALES:
        for key in VALIDATION_KEYS:
            leaked = ENGLISH_FUNCTION_WORDS.search(
                re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "", str(packs[locale].get(key, ""))),
            )
            assert leaked is None, (locale, key, leaked.group(0) if leaked else "")


def test_error_and_warning_states_exist_in_both_themes() -> None:
    for selector in (
        ".world-player-editor-issues{",
        ".world-player-editor-issues-warn{",
        '.world-player-editor-dialog input[aria-invalid="true"]{',
    ):
        assert selector in CSS, selector
    for selector in (
        ':root[data-theme="dark"] .world-player-editor-issues {',
        ':root[data-theme="dark"] .world-player-editor-issues-warn {',
        ':root[data-theme="dark"] .world-player-editor-dialog input[aria-invalid="true"] {',
    ):
        assert selector in DARK_CSS, selector
