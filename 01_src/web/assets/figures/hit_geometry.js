/* Picking against the displayed, already validated detached SVG.
 * No SVG nodes enter the active document. Canvas supplies native Bezier/fill/
 * stroke tests; rectangles in the manifest only reject coarse candidates.
 */
(function () {
  'use strict';
  var SHAPES = new Set(['path', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon']);
  var IGNORE = new Set(['defs', 'clipPath', 'marker', 'linearGradient', 'radialGradient', 'title', 'desc']);
  var PAINT = ['fill', 'stroke', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'stroke-dashoffset',
    'fill-opacity', 'stroke-opacity', 'visibility', 'marker-start', 'marker-mid', 'marker-end', 'font-family', 'font-size', 'font-weight', 'font-style', 'text-anchor', 'dominant-baseline'];
  var MAX_NODES = 100000, MAX_DEPTH = 64;
  function children(node) { return Array.prototype.slice.call(node.children); }
  function numbers(value) {
    var text = String(value || '').trim();
    if (!text) return [];
    var values = text.match(/[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:e[-+]?\d+)?/gi) || [];
    if (text.replace(/[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:e[-+]?\d+)?/gi, '').replace(/[\s,]/g, '') ||
        values.some(function (v) { return !Number.isFinite(Number(v)); })) throw new Error('invalid_geometry_numbers');
    return values.map(Number);
  }
  function number(node, key, fallback) {
    var raw = node.getAttribute(key), value = raw === null ? fallback : Number(raw);
    if (!Number.isFinite(value)) throw new Error('invalid_geometry_number');
    return value;
  }
  function transform(value) {
    var matrix = new DOMMatrix(), rest = String(value || '').trim(), match;
    while (rest) {
      match = /^([a-zA-Z]+)\s*\(([^)]*)\)\s*,?\s*/.exec(rest);
      if (!match) throw new Error('unsupported_geometry_transform');
      var args = numbers(match[2]), op = new DOMMatrix();
      if (match[1] === 'matrix' && args.length === 6) op = new DOMMatrix(args);
      else if (match[1] === 'translate' && [1, 2].includes(args.length)) op = op.translate(args[0], args[1] || 0);
      else if (match[1] === 'scale' && [1, 2].includes(args.length)) op = op.scale(args[0], args.length === 1 ? args[0] : args[1]);
      else if (match[1] === 'rotate' && [1, 3].includes(args.length)) op = args.length === 1 ? op.rotate(args[0]) : op.translate(args[1], args[2]).rotate(args[0]).translate(-args[1], -args[2]);
      else if (match[1] === 'skewX' && args.length === 1) op = op.skewX(args[0]);
      else if (match[1] === 'skewY' && args.length === 1) op = op.skewY(args[0]);
      else throw new Error('unsupported_geometry_transform');
      matrix = matrix.multiply(op); rest = rest.slice(match[0].length);
    }
    if (![matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f].every(Number.isFinite)) throw new Error('invalid_geometry_transform');
    return matrix;
  }
  function inverse(matrix) {
    var result = matrix.inverse();
    if (![result.a, result.b, result.c, result.d, result.e, result.f].every(Number.isFinite)) throw new Error('singular_geometry_transform');
    return result;
  }
  function shape(node) {
    var name = node.localName, p = new Path2D(), x, y, w, h, rx, ry;
    if (name === 'path') return new Path2D(node.getAttribute('d') || '');
    if (name === 'rect') {
      x = number(node, 'x', 0); y = number(node, 'y', 0); w = number(node, 'width', 0); h = number(node, 'height', 0);
      if (w < 0 || h < 0) throw new Error('invalid_geometry_size');
      rx = Math.min(w / 2, number(node, 'rx', number(node, 'ry', 0)));
      ry = Math.min(h / 2, number(node, 'ry', number(node, 'rx', 0)));
      if (rx < 0 || ry < 0) throw new Error('invalid_geometry_radius');
      if (!rx || !ry) p.rect(x, y, w, h);
      else {
        p.moveTo(x + rx, y); p.lineTo(x + w - rx, y); p.ellipse(x + w - rx, y + ry, rx, ry, 0, -Math.PI / 2, 0);
        p.lineTo(x + w, y + h - ry); p.ellipse(x + w - rx, y + h - ry, rx, ry, 0, 0, Math.PI / 2);
        p.lineTo(x + rx, y + h); p.ellipse(x + rx, y + h - ry, rx, ry, 0, Math.PI / 2, Math.PI);
        p.lineTo(x, y + ry); p.ellipse(x + rx, y + ry, rx, ry, 0, Math.PI, Math.PI * 1.5); p.closePath();
      }
    } else if (name === 'circle' || name === 'ellipse') {
      rx = number(node, name === 'circle' ? 'r' : 'rx', 0); ry = name === 'circle' ? rx : number(node, 'ry', 0);
      if (rx < 0 || ry < 0) throw new Error('invalid_geometry_radius');
      p.ellipse(number(node, 'cx', 0), number(node, 'cy', 0), rx, ry, 0, 0, Math.PI * 2);
    } else if (name === 'line') {
      p.moveTo(number(node, 'x1', 0), number(node, 'y1', 0)); p.lineTo(number(node, 'x2', 0), number(node, 'y2', 0));
    } else {
      var points = numbers(node.getAttribute('points'));
      if (points.length % 2) throw new Error('invalid_geometry_points');
      for (var i = 0; i < points.length; i += 2) { if (i) p.lineTo(points[i], points[i + 1]); else p.moveTo(points[i], points[i + 1]); }
      if (name === 'polygon') p.closePath();
    }
    return p;
  }
  // Conservative control hulls are a broad phase only. Arc paths and text
  // without a cheap proven bound stay in the global bucket and use native tests.
  function bounds(node) {
    var n = node.localName, x, y, rx, ry;
    if (n === 'circle' || n === 'ellipse') {
      x = number(node, 'cx', 0); y = number(node, 'cy', 0);
      rx = number(node, n === 'circle' ? 'r' : 'rx', 0); ry = n === 'circle' ? rx : number(node, 'ry', 0);
      return [x - rx, y - ry, x + rx, y + ry];
    }
    if (n === 'rect') { x = number(node, 'x', 0); y = number(node, 'y', 0); return [x, y, x + number(node, 'width', 0), y + number(node, 'height', 0)]; }
    var points = [];
    if (n === 'line') points = [number(node, 'x1', 0), number(node, 'y1', 0), number(node, 'x2', 0), number(node, 'y2', 0)];
    else if (n === 'polygon' || n === 'polyline') points = numbers(node.getAttribute('points'));
    else if (n === 'path') {
      var data = node.getAttribute('d') || '';
      if (/[aAstT]/.test(data)) return null; // reflected/elliptic extrema are not guessed
      var tokens = data.match(/[a-zA-Z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:e[-+]?\d+)?/g) || [];
      var i = 0, command = '', startX = 0, startY = 0; x = 0; y = 0;
      while (i < tokens.length) {
        if (/^[a-zA-Z]$/.test(tokens[i])) command = tokens[i++];
        var upper = command.toUpperCase(), relative = command !== upper;
        if (upper === 'Z') { x = startX; y = startY; command = ''; continue; }
        var length = {M: 2, L: 2, H: 1, V: 1, C: 6, Q: 4}[upper];
        if (!length || i + length > tokens.length) return null;
        var args = tokens.slice(i, i + length).map(Number); i += length;
        if (!args.every(Number.isFinite)) return null;
        if (upper === 'H') { x = args[0] + (relative ? x : 0); points.push(x, y); }
        else if (upper === 'V') { y = args[0] + (relative ? y : 0); points.push(x, y); }
        else {
          for (var j = 0; j < length; j += 2) points.push(args[j] + (relative ? x : 0), args[j + 1] + (relative ? y : 0));
          x = points[points.length - 2]; y = points[points.length - 1];
          if (upper === 'M') { startX = x; startY = y; command = relative ? 'l' : 'L'; }
        }
      }
    } else return null;
    if (!points.length) return null;
    var box = [Infinity, Infinity, -Infinity, -Infinity];
    for (var k = 0; k < points.length; k += 2) {
      box[0] = Math.min(box[0], points[k]); box[1] = Math.min(box[1], points[k + 1]);
      box[2] = Math.max(box[2], points[k]); box[3] = Math.max(box[3], points[k + 1]);
    }
    return box;
  }
  function style(node, parent) {
    var result = Object.assign({}, parent);
    PAINT.forEach(function (key) { if (node.hasAttribute(key)) result[key] = node.getAttribute(key); });
    result.opacity = parent.opacity * Math.max(0, Math.min(1, number(node, 'opacity', 1)));
    return result;
  }
  function textStyle(ctx, paint) {
    ctx.font = paint['font-style'] + ' ' + paint['font-weight'] + ' ' + paint['font-size'].replace(/(?:px)?$/, 'px') + ' ' + paint['font-family'];
    ctx.textAlign = {start: 'left', middle: 'center', end: 'right'}[paint['text-anchor']] || 'left'; ctx.textBaseline = 'alphabetic';
  }
  function strokeStyle(ctx, paint) {
    ctx.lineWidth = Number(paint['stroke-width']);
    ctx.lineCap = paint['stroke-linecap']; ctx.lineJoin = paint['stroke-linejoin']; ctx.miterLimit = 4;
    ctx.setLineDash(paint['stroke-dasharray'] === 'none' ? [] : numbers(paint['stroke-dasharray']));
    ctx.lineDashOffset = Number(paint['stroke-dashoffset']);
  }
  function Geometry(doc, manifest, coarse) {
    if (!window.Path2D || !window.DOMMatrix) throw new Error('geometry_browser_unsupported');
    this.root = doc.documentElement; this.objects = new Map(); this.primitives = []; this.unsupported = new Set();
    this.viewBox = numbers(this.root.getAttribute('viewBox'));
    if (this.viewBox.length !== 4 || this.viewBox[2] <= 0 || this.viewBox[3] <= 0) throw new Error('geometry_viewbox_required');
    this.coarse = coarse !== false;
    var canvas = document.createElement('canvas'); canvas.width = canvas.height = 3;
    this.ctx = canvas.getContext('2d', {willReadFrequently: true});
    if (!this.ctx) throw new Error('geometry_canvas_unavailable');
    var self = this, ids = new Map(), mapping = new Map(), count = 0, colours = new Map();
    function hasPaint(value) {
      if (value === 'none' || value === 'transparent') return false;
      if (/url\s*\(/i.test(value)) throw new Error('unsupported_gradient_geometry');
      if (!colours.has(value)) {
        self.ctx.clearRect(0, 0, 3, 3); self.ctx.fillStyle = '#000'; self.ctx.fillStyle = value;
        self.ctx.fillRect(0, 0, 1, 1); colours.set(value, self.ctx.getImageData(0, 0, 1, 1).data[3] > 0);
      }
      return colours.get(value);
    }
    Array.prototype.forEach.call(doc.querySelectorAll('[id]'), function (node) {
      var id = node.getAttribute('id');
      // The renderer can emit several drawn members with the same series
      // identity. They all belong to that explicit semantic mapping. A clip
      // reference, however, must resolve to exactly one definition below.
      if (!ids.has(id)) ids.set(id, []);
      ids.get(id).push(node);
    });
    (manifest.objects || []).forEach(function (object) {
      if (self.objects.has(object.gid)) throw new Error('ambiguous_manifest_identity');
      self.objects.set(object.gid, object);
      (object.svg_nodes || [object.gid]).forEach(function (id) {
        if (!mapping.has(id)) mapping.set(id, []);
        mapping.get(id).push(object.gid);
      });
    });
    function markUnsupported(owners) { owners.forEach(function (gid) { self.unsupported.add(gid); }); }
    function clips(node, matrix, inherited) {
      var clip = node.getAttribute('clip-path');
      if (!clip || clip === 'none') return inherited;
      var match = /^url\(#([A-Za-z0-9_.:-]+)\)$/.exec(clip), definitions = match && ids.get(match[1]);
      var source = definitions && definitions.length === 1 && definitions[0];
      if (!source || source.localName !== 'clipPath' || !['userSpaceOnUse', null].includes(source.getAttribute('clipPathUnits'))) throw new Error('unsupported_geometry_clip');
      var paths = [];
      function visit(item, parent, depth) {
        if (++count > MAX_NODES || depth > MAX_DEPTH || item.hasAttribute('clip-path')) throw new Error('unsupported_geometry_clip');
        var m = parent.multiply(transform(item.getAttribute('transform')));
        if (SHAPES.has(item.localName)) paths.push({path: shape(item), inverse: inverse(m)});
        else if (item.localName === 'g' || item.localName === 'clipPath') children(item).forEach(function (child) { visit(child, m, depth + 1); });
        else throw new Error('unsupported_geometry_clip');
      }
      visit(source, matrix, 0);
      return inherited.concat([paths]); // union within each clip; intersection between ancestors
    }
    function visit(node, matrix, paint, clip, owners, depth) {
      if (++count > MAX_NODES || depth > MAX_DEPTH) throw new Error('geometry_budget_exceeded');
      if (IGNORE.has(node.localName)) return;
      var own = mapping.get(node.getAttribute('id')) || [];
      if (own.length > 1) { markUnsupported(owners.concat(own)); return; }
      owners = owners.concat(own); // nearest semantic owner wins; backgrounds are excluded below
      try {
        if (node.localName === 'svg' && node !== self.root) throw new Error('unsupported_nested_viewport');
        matrix = matrix.multiply(transform(node.getAttribute('transform'))); inverse(matrix);
        paint = style(node, paint); clip = clips(node, matrix, clip);
        if (SHAPES.has(node.localName) || node.localName === 'text') {
          var eligible = owners.filter(function (gid) { var object = self.objects.get(gid);
            return !['figure', 'axes'].includes(object.role) && !['Figure', 'Axes', 'AxesSubplot'].includes(object.artist) && (object.locator || {}).scope !== 'figure'; });
          if (!eligible.length) return;
          var primitive = {owners: eligible, matrix: matrix, inverse: inverse(matrix), paint: paint, clips: clip, order: self.primitives.length};
          primitive.fillPainted = hasPaint(paint.fill); primitive.strokePainted = hasPaint(paint.stroke);
          if (node.localName === 'text') {
            // Simple text uses the browser's actual glyph mask, including holes.
            // Positioned tspans, complex baselines and bidi need renderer geometry.
            if (node.children.length || node.textContent.length > 4096 || !['auto', 'alphabetic'].includes(paint['dominant-baseline']) || /[֐-ࣿ\n\t]/.test(node.textContent) ||
                !/^\d+(?:\.\d+)?(?:px)?$/.test(paint['font-size'])) throw new Error('unsupported_text_geometry');
            primitive.text = node.textContent; primitive.x = number(node, 'x', 0); primitive.y = number(node, 'y', 0);
            textStyle(self.ctx, paint);
            var metrics = self.ctx.measureText(primitive.text);
            var ink = [primitive.x - metrics.actualBoundingBoxLeft, primitive.y - metrics.actualBoundingBoxAscent,
              primitive.x + metrics.actualBoundingBoxRight, primitive.y + metrics.actualBoundingBoxDescent];
            if (ink.every(Number.isFinite)) primitive.bounds = ink;
          } else { primitive.path = shape(node); primitive.bounds = bounds(node); }
          if (['marker-start', 'marker-mid', 'marker-end'].some(function (key) { return paint[key] && paint[key] !== 'none'; })) throw new Error('unsupported_marker_geometry');
          self.primitives.push(primitive);
        } else children(node).forEach(function (child) { visit(child, matrix, paint, clip, owners, depth + 1); });
      } catch (error) {
        if (error.message === 'geometry_budget_exceeded') throw error;
        markUnsupported(owners);
        // Keep unsupported groups selectable through the tree. Never substitute
        // their bounding rectangle for an unproven geometry hit.
      }
    }
    visit(this.root, new DOMMatrix(), {'fill': 'black', 'stroke': 'none', 'stroke-width': '1', 'stroke-linecap': 'butt',
      'stroke-linejoin': 'miter', 'stroke-dasharray': 'none', 'stroke-dashoffset': '0', 'fill-opacity': '1', 'stroke-opacity': '1',
      'visibility': 'visible', 'font-family': 'sans-serif', 'font-size': '16', 'font-weight': 'normal', 'font-style': 'normal',
      'text-anchor': 'start', 'dominant-baseline': 'auto', opacity: 1}, [], [], 0);
    this.grid = new Map(); this.global = [];
    this.primitives.forEach(function (item) {
      var b = item.bounds;
      if (!b) { self.global.push(item); return; }
      var m = item.matrix, corners = [[b[0], b[1]], [b[2], b[1]], [b[0], b[3]], [b[2], b[3]]].map(function (p) { return m.transformPoint({x: p[0], y: p[1]}); });
      var pad = Math.max(0, Number(item.paint['stroke-width']) || 0) * 4 * Math.hypot(m.a, m.b, m.c, m.d);
      var left = Math.min.apply(null, corners.map(function (p) { return p.x; })) - pad;
      var top = Math.min.apply(null, corners.map(function (p) { return p.y; })) - pad;
      var right = Math.max.apply(null, corners.map(function (p) { return p.x; })) + pad;
      var bottom = Math.max.apply(null, corners.map(function (p) { return p.y; })) + pad;
      var v = self.viewBox;
      var x0 = Math.max(0, Math.floor((left - v[0]) / v[2] * 32)), y0 = Math.max(0, Math.floor((top - v[1]) / v[3] * 32));
      var x1 = Math.min(31, Math.floor((right - v[0]) / v[2] * 32)), y1 = Math.min(31, Math.floor((bottom - v[1]) / v[3] * 32));
      if ((x1 - x0 + 1) * (y1 - y0 + 1) > 256) { self.global.push(item); return; }
      for (var y = y0; y <= y1; y++) for (var x = x0; x <= x1; x++) {
        var key = y * 32 + x;
        if (!self.grid.has(key)) self.grid.set(key, []);
        self.grid.get(key).push(item);
      }
    });
  }
  Geometry.prototype.point = function (clientX, clientY, rect) {
    if (!rect.width || !rect.height || ![clientX, clientY, rect.left, rect.top, rect.width, rect.height].every(Number.isFinite)) return null;
    // The product canvas has the SVG's aspect ratio. Reject a differently
    // letterboxed embedding instead of silently applying the wrong inverse.
    var v = this.viewBox;
    if (Math.abs((rect.width / rect.height) / (v[2] / v[3]) - 1) > 0.002) return null;
    var x = (clientX - rect.left) * v[2] / rect.width + v[0], y = (clientY - rect.top) * v[3] / rect.height + v[1];
    return x < v[0] || y < v[1] || x > v[0] + v[2] || y > v[1] + v[3] ? null : {x: x, y: y};
  };
  Geometry.prototype.hits = function (point, advanced) {
    if (!point) return [];
    var self = this, found = new Map(), ctx = this.ctx;
    function filled(path, inv) { var p = inv.transformPoint(point); return ctx.isPointInPath(path, p.x, p.y, 'nonzero'); }
    function glyph(item, local, fill, stroke) {
      // Only a three-pixel query window is rasterised, never a full image per
      // label. The 4x canonical mask is independent of devicePixelRatio.
      ctx.clearRect(0, 0, 3, 3); ctx.save();
      ctx.setTransform(4, 0, 0, 4, 1.5 - local.x * 4, 1.5 - local.y * 4);
      textStyle(ctx, item.paint);
      ctx.fillStyle = ctx.strokeStyle = '#000';
      if (fill) ctx.fillText(item.text, item.x, item.y);
      if (stroke) { strokeStyle(ctx, item.paint); ctx.strokeText(item.text, item.x, item.y); }
      ctx.restore(); return ctx.getImageData(1, 1, 1, 1).data[3] > 0;
    }
    var v = this.viewBox, column = Math.max(0, Math.min(31, Math.floor((point.x - v[0]) / v[2] * 32)));
    var row = Math.max(0, Math.min(31, Math.floor((point.y - v[1]) / v[3] * 32)));
    this.global.concat(this.grid.get(row * 32 + column) || []).forEach(function (item) {
      var paint = item.paint, owner = self.objects.get(item.owners[item.owners.length - 1]);
      if (paint.opacity <= 0 || paint.visibility === 'hidden' || paint.visibility === 'collapse') return;
      if (self.coarse && owner.bbox && item.text === undefined) {
        var b = owner.bbox, pad = Math.max(1, Number(paint['stroke-width']) || 0);
        if (point.x < b.x0 - pad || point.x > b.x1 + pad || point.y < b.y0 - pad || point.y > b.y1 + pad) return;
      }
      if (!item.clips.every(function (union) { return union.some(function (clip) { return filled(clip.path, clip.inverse); }); })) return;
      var fill = item.fillPainted && Number(paint['fill-opacity']) > 0;
      var stroke = item.strokePainted && Number(paint['stroke-opacity']) > 0 && Number(paint['stroke-width']) > 0;
      var p = item.inverse.transformPoint(point), hit;
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      if (item.text !== undefined) hit = glyph(item, p, fill, stroke);
      else {
        hit = fill && ctx.isPointInPath(item.path, p.x, p.y, 'nonzero');
        if (!hit && stroke) { strokeStyle(ctx, paint); hit = ctx.isPointInStroke(item.path, p.x, p.y); }
      }
      if (!hit) return;
      if (!advanced && ['point', 'bar'].includes(owner.role)) {
        var parent = self.objects.get(owner.parent_gid || (owner.locator || {}).parent_gid);
        if (parent && parent.role === 'series') owner = parent;
      }
      found.set(owner.gid, item.order);
    });
    return Array.from(found).sort(function (a, b) { return b[1] - a[1] || a[0].localeCompare(b[0]); }).map(function (entry) { return entry[0]; });
  };
  window.FigureHitScene = Geometry;
})();
