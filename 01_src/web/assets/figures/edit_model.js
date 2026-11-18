/* Draft records share the server's {gid, prop, value} vocabulary. */
(function () {
  'use strict';
  function copy(value) { return JSON.parse(JSON.stringify(value)); }
  function same(a, b) { return JSON.stringify(a) === JSON.stringify(b); }
  function map(batch) {
    var result = Object.create(null);
    (batch || []).forEach(function (item) {
      if (!result[item.gid]) result[item.gid] = Object.create(null);
      result[item.gid][item.prop] = copy(item.value);
    });
    return result;
  }
  function batch(edits) {
    var result = [];
    Object.keys(edits).sort().forEach(function (gid) {
      Object.keys(edits[gid]).sort().forEach(function (prop) {
        result.push({ gid: gid, prop: prop, value: copy(edits[gid][prop]) });
      });
    });
    return result;
  }
  // Difference against the exact scene, including reverting a previous preview.
  function delta(edits, baseline, savedValue, preserveEqualEdit) {
    var result = [], keys = new Set(batch(edits).concat(batch(baseline)).map(function (item) {
      return JSON.stringify([item.gid, item.prop]);
    }));
    keys.forEach(function (key) {
      var pair = JSON.parse(key), gid = pair[0], prop = pair[1];
      var value = edits[gid] && edits[gid][prop] !== undefined ? edits[gid][prop] : savedValue(gid, prop);
      var before = baseline[gid] && baseline[gid][prop] !== undefined ? baseline[gid][prop] : savedValue(gid, prop);
      // An explicit value can replace an inherited mode without changing RGB.
      var explicit = edits[gid] && edits[gid][prop] !== undefined;
      if (!same(value, before) || (explicit && preserveEqualEdit && preserveEqualEdit(gid, prop))) {
        result.push({ gid: gid, prop: prop, value: value });
      }
    });
    return result;
  }
  window.FigureEditModel = Object.freeze({ copy: copy, same: same, map: map, batch: batch, delta: delta });
})();
