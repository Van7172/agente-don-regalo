"use strict";

const assert = require("node:assert/strict");
require("../public/assets/inbox-time.js");

const {
  conversationTimeLabel,
  localDayKey,
} = globalThis.CrmInboxTime;

// Sin offset: se evalúan como fechas locales en cualquier zona del runner.
const now = "2026-07-27T18:30:00";

assert.equal(
  conversationTimeLabel("2026-07-27T15:00:00", now),
  "15:00",
  "un mensaje de hoy debe mostrar la hora"
);
assert.equal(
  conversationTimeLabel("2026-07-26T15:00:00", now),
  "Ayer",
  "un mensaje de ayer debe mostrar Ayer"
);
assert.equal(
  conversationTimeLabel("2026-07-25T15:00:00", now),
  "25/07/2026",
  "un mensaje anterior debe mostrar la fecha exacta"
);
assert.equal(localDayKey(now), "2026-07-27");
assert.equal(localDayKey(new Date(2026, 6, 27, 23, 59)), "2026-07-27");

console.log("inbox time contract: OK");
