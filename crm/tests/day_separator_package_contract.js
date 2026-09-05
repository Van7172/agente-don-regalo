"use strict";

/**
 * Cada día es un paquete (`.day-group`) con su propia nubesita sticky.
 * Sin el wrapper, todos los `.day-sep { position: sticky; top: 0 }` viven
 * en el mismo scroll y se apilan unos encima de otros ("Ayer" sobre "19 agosto").
 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const root = path.join(__dirname, "..", "public", "assets");
const inboxJs = fs.readFileSync(path.join(root, "inbox.js"), "utf8");
const appCss = fs.readFileSync(path.join(root, "app.css"), "utf8");

assert.match(
  inboxJs,
  /className\s*=\s*["']day-group["']/,
  "threadNodes debe envolver cada jornada en .day-group"
);
assert.match(
  inboxJs,
  /day-sep/,
  "sigue existiendo el separador de día"
);

// El sticky queda, pero limitado al paquete del día (no al thread entero).
assert.match(
  appCss,
  /\.day-group\s*\{[^}]*display:\s*flex/s,
  ".day-group agrupa el paquete del día"
);
assert.match(
  appCss,
  /\.day-sep\s*\{[^}]*position:\s*sticky/s,
  "la nubesita sigue sticky dentro de su paquete"
);

console.log("day separator package contract: OK");
