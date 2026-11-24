// Helpers shared by the landing page and the app. One global, window.Resibo; nothing else leaks.
// Every value from the API is inserted with textContent or attribute setters, never as HTML.
(function () {
  "use strict";

  function el(tag, options, children) {
    var node = document.createElement(tag);
    options = options || {};
    if (options.className) node.className = options.className;
    if (options.text !== undefined && options.text !== null) node.textContent = options.text;
    if (options.attrs) {
      Object.keys(options.attrs).forEach(function (name) {
        node.setAttribute(name, options.attrs[name]);
      });
    }
    (children || []).forEach(function (child) {
      if (child) node.appendChild(child);
    });
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // --- formatting ---------------------------------------------------------------------------

  function plainNumber(value) {
    return Number(value).toLocaleString("en-PH", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }

  // Money in the record's currency. An unknown or odd currency code makes Intl throw a
  // RangeError: fall back to a plain two-decimal number with the raw currency text.
  function formatMoney(value, currency) {
    if (value === null || value === undefined) return "—";
    if (currency) {
      try {
        return new Intl.NumberFormat("en-PH", { style: "currency", currency: currency }).format(value);
      } catch (err) {
        // RangeError for an invalid currency code
      }
    }
    return plainNumber(value) + (currency ? " " + currency : "");
  }

  function dash(value) {
    return value === null || value === undefined || value === "" ? "—" : String(value);
  }

  function formatPages(pages) {
    if (!pages || !pages.length) return "—";
    var sorted = pages.slice().sort(function (a, b) { return a - b; });
    var parts = [];
    var start = sorted[0];
    var prev = start;
    for (var i = 1; i <= sorted.length; i++) {
      if (sorted[i] === prev + 1) {
        prev = sorted[i];
        continue;
      }
      parts.push(start === prev ? String(start) : start + "-" + prev);
      start = sorted[i];
      prev = start;
    }
    return parts.join(", ");
  }

  function plural(count, one, many) {
    return count + " " + (count === 1 ? one : many);
  }

  // --- totals -------------------------------------------------------------------------------

  // One entry per currency of summary.total_amount_by_currency. The amounts come from the two
  // summary fields (the server computes them once); this function only subtracts and counts.
  //   amount:  the total excluding errors, or null when every invoice in that currency is ERROR
  //   leftOut: what the ERROR invoices add to the all-documents total
  //   errors:  the ERROR invoices in that currency (one without a total still counts)
  function totalLines(summary, invoices) {
    var all = (summary && summary.total_amount_by_currency) || {};
    var excluding = (summary && summary.total_excluding_errors_by_currency) || {};
    return Object.keys(all).map(function (currency) {
      var kept = excluding[currency];
      var has = kept !== undefined && kept !== null;
      var leftOut = Math.round((all[currency] - (has ? kept : 0)) * 100) / 100;
      var errors = (invoices || []).filter(function (inv) {
        return inv.status === "ERROR" && inv.currency === currency;
      }).length;
      return { currency: currency, amount: has ? kept : null, leftOut: leftOut, errors: errors };
    });
  }

  window.Resibo = {
    el: el,
    clear: clear,
    plainNumber: plainNumber,
    formatMoney: formatMoney,
    dash: dash,
    formatPages: formatPages,
    plural: plural,
    totalLines: totalLines,
  };
})();
