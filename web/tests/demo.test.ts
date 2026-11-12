import assert from "node:assert/strict";
import { test } from "node:test";
import { createDemoStore, getVisibleResult } from "../src/lib/demo";

// These tests catch out-of-bounds navigation, stale result leakage, and shared instances.
test("starts at the first step with no premature technical result", () => {
  const store = createDemoStore();
  assert.equal(store.getState().step, 0);
  assert.equal(store.getState().scenario, "success");
  assert.equal(getVisibleResult(store.getState()), null);
});
test("previous and next stop at the first and fifth steps", () => {
  const store = createDemoStore();
  store.getState().previous();
  assert.equal(store.getState().step, 0);
  for (let i = 0; i < 8; i++) store.getState().next();
  assert.equal(store.getState().step, 4);
  assert.equal(getVisibleResult(store.getState()), "PASS");
});
test("changing scenario resets progress and hides a previous success", () => {
  const store = createDemoStore();
  for (let i = 0; i < 4; i++) store.getState().next();
  store.getState().selectScenario("failure");
  assert.equal(store.getState().step, 0);
  assert.equal(getVisibleResult(store.getState()), null);
  for (let i = 0; i < 4; i++) store.getState().next();
  assert.equal(getVisibleResult(store.getState()), "FAIL");
});
test("missing terminal evidence stays unknown rather than becoming success", () => {
  const store = createDemoStore();
  store.getState().selectScenario("unknown");
  for (let i = 0; i < 4; i++) store.getState().next();
  assert.equal(getVisibleResult(store.getState()), "UNKNOWN");
  store.getState().previous();
  assert.equal(getVisibleResult(store.getState()), null);
});
test("reset retains the chosen scenario but returns to the beginning", () => {
  const store = createDemoStore();
  store.getState().selectScenario("failure");
  store.getState().next();
  store.getState().reset();
  assert.equal(store.getState().scenario, "failure");
  assert.equal(store.getState().step, 0);
});
test("each demo has an independent initial state", () => {
  const first = createDemoStore();
  const second = createDemoStore();
  first.getState().selectScenario("unknown");
  first.getState().next();
  assert.equal(second.getState().scenario, "success");
  assert.equal(second.getState().step, 0);
});
