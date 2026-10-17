import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync("web/ticket-delivery.js", "utf8");

test("bulk delete and refresh share one heading action group, not the status tabs", () => {
  const anchors = new Map(["refresh-tickets", "notification-refresh", "refresh-requests"].map(id => [id, {
    id, before(group) { this.group = group; },
  }]));
  const start = source.indexOf("for (const [target");
  const end = source.indexOf("Object.assign(apiErrorLabels", start);
  vm.runInNewContext(source.slice(start, end), {
    document: {
      getElementById: id => anchors.get(id),
      createElement: () => ({ dataset: {}, children: [], append(...items) { this.children.push(...items); } }),
    },
    ticketStages: { resident: "finished", manager: "finished" },
  });
  for (const [id, anchor] of anchors) {
    assert.equal(anchor.group.className, "section-heading-actions");
    assert.equal(anchor.group.children.length, 2);
    assert.equal(anchor.group.children[1], anchor);
    const deletion = anchor.group.children[0];
    assert.equal(deletion.className, "refresh-button delivery-bulk-delete");
    assert.equal(deletion.dataset.hideList, id === "refresh-requests" ? "management" : id === "refresh-tickets" ? "tickets" : "notifications");
    assert.equal(deletion.disabled, true);
  }
});
