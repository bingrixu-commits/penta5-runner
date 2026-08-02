"use strict";
/**
 * Compatibility facade for Runner — primary engines live in algorithms/<id>/engine.js.
 * Lazy-loads per export; never falls back to a monolith hall.
 */
module.exports = require("./framework/index");
