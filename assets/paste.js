/* Excel-row paste for inline forecast rows.
 *
 * Dash auto-serves every assets/*.js. This script lets a user copy a row from
 * Excel and paste it across a forecast line's channel cells in one action.
 *
 * Scope: only inputs with id {"type":"row-input","row":R,"col":C} — the inline
 * rows (PY, SIOP, Monday FRC, EoW WIP). Those have no comment-required rule.
 * Panel inputs (Friday/Thursday/Actual/WIP OT) use other id types and are left
 * to the browser's default single-cell paste.
 *
 * The pasted values are committed via the native React value setter + input/
 * change events, so they flow into `form-values` through the existing row-input
 * sync clientside callback in app.py — no new Dash wiring.
 */
(function () {
  "use strict";

  // React tracks a controlled input's value internally; a plain `el.value = x`
  // is ignored on the next render. Setting through the prototype descriptor and
  // dispatching an input event is the supported way to drive React state.
  var nativeValueSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype,
    "value"
  ).set;

  function setInputValue(input, value) {
    nativeValueSetter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // Parse a Dash pattern-matching id (JSON string) off an element.
  function parseId(el) {
    if (!el || !el.id) return null;
    try {
      return JSON.parse(el.id);
    } catch (e) {
      return null;
    }
  }

  document.addEventListener("paste", function (e) {
    var target = e.target;
    if (!target || target.tagName !== "INPUT") return;

    var id = parseId(target);
    if (!id || id.type !== "row-input") return;

    var text = (e.clipboardData || window.clipboardData).getData("text");
    if (!text) return;

    // A horizontal Excel row is tab-separated; a vertical copy is newline-
    // separated. Flatten both to one ordered list of values.
    var tokens = text
      .split(/\r\n|\r|\n|\t/)
      .map(function (t) {
        return t.trim();
      })
      .filter(function (t) {
        return t.length > 0;
      });

    // Single value → let the browser paste it normally into this one cell.
    if (tokens.length <= 1) return;

    e.preventDefault();

    // Collect this row's editable cells in DOM order (already channel order).
    // N/A and submitted/locked cells emit no input, so they are skipped for
    // free — values land only on editable channels.
    var rowKey = String(id.row);
    var inputs = Array.prototype.filter.call(
      document.querySelectorAll('input[id*="row-input"]'),
      function (el) {
        var eid = parseId(el);
        return eid && eid.type === "row-input" && String(eid.row) === rowKey;
      }
    );

    var start = inputs.indexOf(target);
    if (start === -1) return;

    for (var i = 0; i < tokens.length && start + i < inputs.length; i++) {
      setInputValue(inputs[start + i], tokens[i]);
    }
  });
})();
