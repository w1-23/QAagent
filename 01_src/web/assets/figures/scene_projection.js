/* Detached SVG drafts. No server node is ever inserted into the active document. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var TAGS = new Set('svg g path rect circle ellipse line polyline polygon text tspan title desc defs linearGradient radialGradient stop clipPath marker'.split(' '));
  var ATTRS = new Set(('viewBox preserveAspectRatio stroke-width stroke-linecap stroke-linejoin stroke-dasharray stroke-dashoffset fill-opacity stroke-opacity font-family font-size font-weight font-style text-anchor dominant-baseline gradientUnits gradientTransform stop-color stop-opacity clipPathUnits markerWidth markerHeight markerUnits marker-start marker-mid marker-end aria-label clip-path role id x y x1 y1 x2 y2 cx cy r rx ry width height d points fill stroke opacity transform offset xmlns').split(' '));
  var TEXT = new Set(['title', 'xlabel', 'ylabel', 'annot', 'ticklabel', 'legend']);
  function list(nodes) { return Array.prototype.slice.call(nodes); }
  function elements(root, selector) {
    return (root.matches(selector) ? [root] : []).concat(list(root.querySelectorAll(selector)));
  }
  function paint(nodes, attr, value) { nodes.forEach(function (node) { node.setAttribute(attr, String(value)); }); }
  function translate(node, dx, dy) {
    node.setAttribute('transform', 'translate(' + dx + ' ' + dy + ') ' + (node.getAttribute('transform') || ''));
  }
  function parse(svg) {
    if (typeof svg !== 'string' || svg.length > 8 * 1024 * 1024 || /<!DOCTYPE|<!ENTITY/i.test(svg)) throw new Error('SVG 不支持本地解析');
    var doc = new DOMParser().parseFromString(svg, 'image/svg+xml');
    if (doc.querySelector('parsererror') || doc.documentElement.localName !== 'svg') throw new Error('SVG 无法解析');
    // The server already emits this closed subset. Reject unexpected constructs
    // instead of silently deleting drawing content or permitting external loads.
    list(doc.querySelectorAll('*')).forEach(function (node) {
      if (node.namespaceURI !== NS || !TAGS.has(node.localName)) throw new Error('SVG 需要服务端校正');
      list(node.attributes).forEach(function (attr) {
        if (!ATTRS.has(attr.name) || /javascript:|data:|expression\s*\(/i.test(attr.value) ||
            (/url\s*\(/i.test(attr.value) && !/^url\(#[A-Za-z0-9_.:-]+\)$/.test(attr.value))) throw new Error('SVG 包含无法本地投影的属性');
      });
    });
    return doc;
  }
  function Scene(svg, manifest) {
    this.base = parse(svg);
    this.manifest = manifest;
    this.objects = new Map((manifest.objects || []).map(function (o) { return [o.gid, o]; }));
  }
  Scene.prototype.project = function (changes, valueBefore, protectedEdits) {
    protectedEdits = protectedEdits || [];
    var sharedGroups = this.manifest.shared_legend_groups || {};
    var effective = new Map(protectedEdits.map(function (item) { return [JSON.stringify([item.gid, item.prop]), item.value]; }));
    var doc = this.base.cloneNode(true), objects = new Map(), failed = [], changed = false;
    this.objects.forEach(function (object, gid) { objects.set(gid, Object.assign({}, object)); });
    changes.forEach(function (edit) {
      var object = objects.get(edit.gid);
      if (object && ['marker', 'markersize', 'markerfacecolor', 'markeredgecolor', 'markeredgewidth', 'linewidth'].indexOf(edit.prop) >= 0) object[edit.prop] = edit.value;
      if (object && edit.prop === 'color') {
        if (object.markerface_auto) object.markerfacecolor = edit.value;
        if (object.markeredge_auto) object.markeredgecolor = edit.value;
      }
    });
    var byId = new Map();
    list(doc.querySelectorAll('[id]')).forEach(function (node) {
      var id = node.getAttribute('id');
      if (!byId.has(id)) byId.set(id, []);
      byId.get(id).push(node);
    });
    function nodesFor(object) {
      var ids = object.svg_nodes || [object.gid];
      return ids.reduce(function (all, id) { return all.concat(byId.get(id) || []); }, []);
    }
    function texts(nodes) { return nodes.reduce(function (all, node) { return all.concat(elements(node, 'text')); }, []); }
    function shapes(nodes) { return nodes.reduce(function (all, node) { return all.concat(elements(node, 'path,rect,circle,ellipse,line,polyline,polygon').filter(function (n) { return !n.closest('defs,clipPath'); })); }, []); }
    function legendLinks(object) {
      return (object.legend_links || []).map(function (link) {
        return { texts: byId.get(link.text_gid) || [], marks: (link.mark_gids || []).reduce(function (all, id) { return all.concat(byId.get(id) || []); }, []),
          style: {svg_markers: (link.mark_gids || []).flatMap(function (id) { return ((link.style_nodes || {})[id] || {}).svg_markers || []; }),
                  svg_lines: (link.mark_gids || []).flatMap(function (id) { return ((link.style_nodes || {})[id] || {}).svg_lines || []; })} };
      });
    }
    function changeText(nodes, value) {
      if (nodes.length !== 1 || nodes[0].children.length || /[\n\t]/.test(value)) return false;
      nodes[0].textContent = value; return true;
    }
    function apply(object, nodes, prop, value, before, linked) {
      var ts = texts(nodes), marks = shapes(nodes), isText = TEXT.has(object.role);
      if (!nodes.length) return false; // hidden artists and scatter children have no SVG identity
      if (prop === 'offset_px') {
        if (!Array.isArray(value) || !Array.isArray(before)) return false;
        nodes.forEach(function (node) { translate(node, value[0] - before[0], value[1] - before[1]); });
        return true;
      }
      if (prop === 'visible') { paint(nodes, 'visibility', value ? 'visible' : 'hidden'); return true; }
      if (prop === 'alpha') {
        // Alpha replaces per-artist alpha; do not compound it on the group.
        if (!marks.length) return false;
        nodes.forEach(function (node) { elements(node, 'g').forEach(function (group) { group.removeAttribute('opacity'); }); });
        marks.forEach(function (node) { node.removeAttribute('fill-opacity'); node.removeAttribute('stroke-opacity'); node.setAttribute('opacity', value); });
        return true;
      }
      if (prop === 'color') {
        if (isText) { if (!ts.length) return false; paint(ts.concat(ts.flatMap(function (t) { return list(t.querySelectorAll('tspan')); })), 'fill', value); return true; }
        if (!marks.length) return false;
        if (object.artist === 'Line2D' && object.markerface_auto === undefined) return false;
        marks.forEach(function (node) {
          var marker = (object.svg_markers || []).indexOf(node.getAttribute('id')) >= 0;
          if (node.getAttribute('fill') !== 'none' && (object.artist !== 'Line2D' || (marker && object.markerface_auto))) node.setAttribute('fill', value);
          if (node.getAttribute('stroke') && node.getAttribute('stroke') !== 'none' && (!marker || object.artist !== 'Line2D' || object.markeredge_auto)) node.setAttribute('stroke', value);
        });
        return true;
      }
      if (prop === 'text') return changeText(ts, value);
      if (prop === 'label') {
        var links = legendLinks(object);
        if (!links.length) return object.legend_links !== undefined;
        return links.every(function (link) { return changeText(link.texts, value); });
      }
      var fontAttr = { fontsize: 'font-size', fontfamily: 'font-family', fontweight: 'font-weight', fontstyle: 'font-style', horizontalalignment: 'text-anchor' }[prop];
      if (fontAttr) {
        if (!ts.length) return false;
        // Mixed/math text and multi-line layout require the renderer's metrics.
        if (ts.some(function (text) { return text.children.length > 0; })) return false;
        if (prop === 'fontfamily' && document.fonts && !document.fonts.check('12px "' + String(value).replace(/["\\]/g, '') + '"')) return false;
        var mapped = prop === 'horizontalalignment' ? {left: 'start', center: 'middle', right: 'end'}[value]
          : prop === 'fontweight' ? {light: 300, normal: 400, medium: 500, semibold: 600, bold: 700, heavy: 800, black: 900}[value] : value;
        paint(ts, fontAttr, mapped); return true;
      }
      if (prop === 'frameon') {
        var frame = object.svg_frame && byId.get(object.svg_frame);
        if (!frame || !frame.length) return false;
        paint(frame, 'opacity', value ? 1 : 0); return true;
      }
      if (prop === 'loc' || prop === 'ncols') return layoutLegend(object, nodes, prop, value);
      if (prop === 'linewidth') {
        if (!marks.length) return false;
        if (object.artist === 'Line2D' && object.svg_lines === undefined) return false;
        paint(marks.filter(function (node) { return !/\.marker_\d+$/.test(node.getAttribute('id') || ''); }), 'stroke-width', value);
        if (object.artist === 'Line2D' && object.linestyle && object.linestyle !== 'None' && (object.svg_lines || []).length) {
          return apply(object, nodes, 'linestyle', object.linestyle, before, linked);
        }
        return true;
      }
      // A renderer-provided marker identity/geometry separates markers from
      // data paths; legacy flattened paths must not be guessed by vertex count.
      var markerNodes = (object.svg_markers || []).reduce(function (all, id) { return all.concat(byId.get(id) || []); }, []);
      var lineNodes = (object.svg_lines || []).reduce(function (all, id) { return all.concat(byId.get(id) || []); }, []);
      if (prop === 'linestyle') {
        if (!lineNodes.length) return false;
        var width = Number(object.linewidth || 1), dash = {'-': '', '--': [3.7, 1.6], '-.': [6.4, 1.6, 1, 1.6], ':': [1, 1.65]}[value];
        lineNodes.forEach(function (node) {
          if (value === 'None') node.setAttribute('opacity', '0');
          else { node.setAttribute('stroke-dasharray', dash ? dash.map(function (n) { return n * width; }).join(' ') : 'none'); node.setAttribute('stroke-dashoffset', '0'); }
        });
        return true;
      }
      if (!markerNodes.length) return false;
      if (prop === 'markerfacecolor' || prop === 'markeredgecolor' || prop === 'markeredgewidth') {
        paint(markerNodes, {markerfacecolor: 'fill', markeredgecolor: 'stroke', markeredgewidth: 'stroke-width'}[prop], value); return true;
      }
      if (prop === 'markersize' || prop === 'marker') {
        var marker = prop === 'marker' ? value : object.marker;
        var size = prop === 'markersize' ? value : object.markersize;
        if (!Number.isFinite(size) || !markerPaths[marker]) return false;
        markerNodes.forEach(function (node) {
          node.setAttribute('d', markerPaths[marker](size));
          node.setAttribute('fill', ['+', 'x', 'None'].indexOf(marker) >= 0 ? 'none' : object.markerfacecolor || 'none');
          node.setAttribute('stroke', object.markeredgecolor || 'none');
          if (object.markeredgewidth !== undefined) node.setAttribute('stroke-width', object.markeredgewidth);
        });
        return true;
      }
      return false;
    }
    function layoutLegend(object, nodes, prop, value) {
      var layout = object.svg_legend;
      if (!layout || !layout.rows.length || nodes.length !== 1 || layout.custom_anchor !== false) return false;
      if (prop === 'loc') {
        if (value === 'best' || !layout.axes_bbox || !object.bbox) return false;
        var a = layout.axes_bbox, b = object.bbox, pad = layout.borderaxespad;
        var x = /left/.test(value) ? a.x0 + pad : /right/.test(value) || value === 'right' ? a.x1 - pad - (b.x1 - b.x0) : (a.x0 + a.x1 - (b.x1 - b.x0)) / 2;
        var y = /upper/.test(value) ? a.y0 + pad : /lower/.test(value) ? a.y1 - pad - (b.y1 - b.y0) : (a.y0 + a.y1 - (b.y1 - b.y0)) / 2;
        var offset = valueBefore(object.gid, 'offset_px') || [0, 0];
        translate(nodes[0], x + offset[0] - b.x0, y + offset[1] - b.y0); return true;
      }
      // Repack rows using measured renderer bounds. Layout grows from the same
      // anchor; the final server render supplies exact font metrics on save.
      if (layout.has_title || layout.mode === 'expand' || !object.loc || object.loc === 'best') return false;
      var rows = layout.rows, count = Math.min(value, rows.length), index = 0, x = layout.origin[0], maxHeight = 0;
      var moves = [];
      for (var col = 0; col < count; col++) {
        var length = Math.floor(rows.length / count) + (col < rows.length % count ? 1 : 0), y = layout.origin[1], width = 0;
        for (var row = 0; row < length; row++) {
          var item = rows[index++];
          if (!item.bbox || !item.gids.every(function (id) { return byId.has(id); })) return false;
          moves.push({ item: item, x: x, y: y });
          width = Math.max(width, item.bbox.x1 - item.bbox.x0);
          y += item.bbox.y1 - item.bbox.y0 + layout.row_gap;
        }
        x += width + layout.column_gap; maxHeight = Math.max(maxHeight, y - layout.origin[1] - layout.row_gap);
      }
      moves.forEach(function (move) { move.item.gids.forEach(function (id) { byId.get(id).forEach(function (node) { translate(node, move.x - move.item.bbox.x0, move.y - move.item.bbox.y0); }); }); });
      var frame = object.svg_frame && byId.get(object.svg_frame);
      var width = x - layout.origin[0] - layout.column_gap + 2 * layout.padding;
      var height = maxHeight + 2 * layout.padding;
      if (object.bbox) {
        var dw = object.bbox.x1 - object.bbox.x0 - width, dh = object.bbox.y1 - object.bbox.y0 - height;
        translate(nodes[0], /right/.test(object.loc) || object.loc === 'right' ? dw : /left/.test(object.loc) ? 0 : dw / 2,
          /lower/.test(object.loc) ? dh : /upper/.test(object.loc) ? 0 : dh / 2);
      }
      if (frame && frame.length) {
        if (frame[0].localName === 'rect') {
          frame[0].setAttribute('width', width); frame[0].setAttribute('height', height);
        } else if (frame[0].localName === 'g' && frame[0].querySelector('path') && object.svg_frame_shape) {
          var shape = object.svg_frame_shape, bounds = shape.bbox, r = Math.min(shape.radius, width / 2, height / 2);
          var left = bounds.x0, top = bounds.y0, right = left + width, bottom = top + height;
          frame[0].querySelector('path').setAttribute('d', 'M ' + (left+r) + ' ' + top + ' H ' + (right-r) +
            ' Q ' + right + ' ' + top + ' ' + right + ' ' + (top+r) + ' V ' + (bottom-r) +
            ' Q ' + right + ' ' + bottom + ' ' + (right-r) + ' ' + bottom + ' H ' + (left+r) +
            ' Q ' + left + ' ' + bottom + ' ' + left + ' ' + (bottom-r) + ' V ' + (top+r) +
            ' Q ' + left + ' ' + top + ' ' + (left+r) + ' ' + top + ' Z');
        } else return false;
      }
      return true;
    }
    // Parents first; child overrides remain stronger, as on the server.
    changes.slice().sort(function (a, b) { return a.gid.split('.').length - b.gid.split('.').length; }).forEach(function (edit) {
      var object = objects.get(edit.gid), before = valueBefore(edit.gid, edit.prop);
      if (!object || edit.value === undefined) { failed.push(edit); return; }
      if (Object.keys(sharedGroups).some(function (key) {
        var group = sharedGroups[key];
        return group.indexOf(edit.gid) >= 0 && group.some(function (gid) {
          return JSON.stringify(effective.get(JSON.stringify([gid, edit.prop]))) !== JSON.stringify(edit.value);
        });
      })) { failed.push(edit); return; }
      if (object.role === 'series' && protectedEdits.some(function (item) {
        var child = objects.get(item.gid);
        return child && child.locator && child.locator.parent_gid === edit.gid;
      })) { failed.push(edit); return; }
      var nodes = nodesFor(object);
      var ok = apply(object, nodes, edit.prop, edit.value, before);
      if (ok && object.role === 'series' && edit.prop !== 'label') {
        var links = legendLinks(object);
        if (object.legend_links === undefined) ok = false;
        else links.forEach(function (link) {
          if (!apply(Object.assign({}, object, link.style), link.marks, edit.prop, edit.value, before, true)) ok = false;
          if (edit.prop === 'visible') paint(link.texts, 'visibility', edit.value ? 'visible' : 'hidden');
        });
      }
      if (!ok) {
        // A failed mapping is discarded with the whole projection below; no
        // partially updated legend/series can masquerade as a finished draft.
        failed.push(edit);
      } else changed = true;
    });
    // Rebuild once without failed edits, retaining all reliably mapped edits.
    if (failed.length && changed) {
      var bad = new Set(failed.map(function (item) { return JSON.stringify([item.gid, item.prop]); }));
      var safe = changes.filter(function (item) { return !bad.has(JSON.stringify([item.gid, item.prop])); });
      var projected = this.project(safe, valueBefore, protectedEdits);
      return { svg: projected.svg, document: projected.document, unsupported: failed.concat(projected.unsupported) };
    }
    return { svg: new XMLSerializer().serializeToString(failed.length ? this.base : doc), document: failed.length ? this.base : doc, unsupported: failed };
  };
  var markerPaths = {
    o: function (s) { var r = s / 2; return 'M ' + r + ' 0 A ' + r + ' ' + r + ' 0 1 0 ' + -r + ' 0 A ' + r + ' ' + r + ' 0 1 0 ' + r + ' 0 Z'; },
    s: function (s) { var r = s / 2; return 'M ' + -r + ' ' + -r + ' h ' + s + ' v ' + s + ' h ' + -s + ' Z'; },
    '^': function (s) { return 'M 0 ' + -s / 2 + ' L ' + -s / 2 + ' ' + s / 2 + ' L ' + s / 2 + ' ' + s / 2 + ' Z'; },
    v: function (s) { return 'M 0 ' + s / 2 + ' L ' + -s / 2 + ' ' + -s / 2 + ' L ' + s / 2 + ' ' + -s / 2 + ' Z'; },
    D: function (s) { var r = s / Math.sqrt(2); return 'M 0 ' + -r + ' L ' + r + ' 0 L 0 ' + r + ' L ' + -r + ' 0 Z'; },
    '+': function (s) { var r = s / 2; return 'M ' + -r + ' 0 H ' + r + ' M 0 ' + -r + ' V ' + r; },
    x: function (s) { var r = s / 2; return 'M ' + -r + ' ' + -r + ' L ' + r + ' ' + r + ' M ' + -r + ' ' + r + ' L ' + r + ' ' + -r; },
    P: function (s) { return polygon(s, [[-.5,-1/6],[-1/6,-1/6],[-1/6,-.5],[1/6,-.5],[1/6,-1/6],[.5,-1/6],[.5,1/6],[1/6,1/6],[1/6,.5],[-1/6,.5],[-1/6,1/6],[-.5,1/6]]); },
    X: function (s) { return polygon(s, [[-.5,-.25],[-.25,-.5],[0,-.25],[.25,-.5],[.5,-.25],[.25,0],[.5,.25],[.25,.5],[0,.25],[-.25,.5],[-.5,.25],[-.25,0]]); },
    '.': function (s) { return markerPaths.o(s / 2); },
    None: function () { return 'M 0 0'; }
  };
  function polygon(size, vertices) { return vertices.map(function (v, i) { return (i ? 'L ' : 'M ') + v[0] * size + ' ' + v[1] * size; }).join(' ') + ' Z'; }
  window.FigureScene = Scene;
})();
