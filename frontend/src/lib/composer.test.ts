import assert from "node:assert/strict";
import test from "node:test";
import { submitsMessage } from "./composer.ts";

test("Enter submits only outside composition, while Shift+Enter keeps a newline", () => {
  const enter = { key: "Enter", shiftKey: false, keyCode: 13, nativeEvent: { isComposing: false } };
  assert.equal(submitsMessage(enter), true);
  assert.equal(submitsMessage({ ...enter, shiftKey: true }), false);
  assert.equal(submitsMessage({ ...enter, nativeEvent: { isComposing: true } }), false);
  assert.equal(submitsMessage({ ...enter, keyCode: 229 }), false);
  assert.equal(submitsMessage({ ...enter, key: "a", keyCode: 65 }), false);
});
