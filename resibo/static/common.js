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

  // "23 Nov 2026" for an ISO timestamp, in Manila time. Falls back to the ISO date when Intl
  // is missing or throws, or the timestamp does not parse.
  function formatManilaDate(iso) {
    var fallback = String(iso || "").slice(0, 10);
    try {
      var when = new Date(iso);
      if (isNaN(when.getTime())) return fallback || "—";
      var parts = {};
      new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Manila",
        day: "numeric",
        month: "short",
        year: "numeric",
      })
        .formatToParts(when)
        .forEach(function (part) {
          parts[part.type] = part.value;
        });
      return parts.day + " " + parts.month + " " + parts.year;
    } catch (err) {
      return fallback || "—";
    }
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

  // The ten fields the landing rack lists as "What Resibo read", in order.
  var SLIP_FIELDS = [
    ["seller_name", "Seller"],
    ["seller_tin", "Seller TIN"],
    ["buyer_name", "Buyer"],
    ["invoice_number", "Invoice no."],
    ["invoice_date", "Date"],
    ["document_type", "Document type"],
    ["vatable_sales", "Vatable sales"],
    ["vat_amount", "VAT"],
    ["total_amount", "Total"],
    ["status", "Status"]
  ];
  var MONEY_FIELDS = ["vatable_sales", "vat_amount", "total_amount"];

  // Everything the rack needs for one invoice: the slip label (page label from source_pages,
  // never from position), the ten fields with "—" for an absent value, and the first flag.
  function slipModel(inv) {
    var flag = inv.flags && inv.flags.length ? inv.flags[0] : null;
    var rejected = inv.status === "ERROR";
    var fields = SLIP_FIELDS.map(function (pair) {
      var raw = inv[pair[0]];
      var value;
      if (MONEY_FIELDS.indexOf(pair[0]) !== -1) value = formatMoney(raw, inv.currency);
      else if (pair[0] === "document_type") value = dash(raw ? String(raw).replace(/_/g, " ") : raw);
      else value = dash(raw);
      return { key: pair[0], label: pair[1], value: value, flagged: !!flag && flag.field === pair[0] };
    });
    return {
      id: inv.id,
      pages: formatPages(inv.source_pages),
      number: dash(inv.invoice_number),
      seller: dash(inv.seller_name),
      status: inv.status,
      label: rejected ? "REJECTED" : inv.status === "REVIEW" ? "REVIEW" : "OK",
      rejected: rejected,
      flagField: flag ? flag.field : null,
      flagMessage: flag ? flag.message : null,
      fields: fields,
      doc: {
        address: dash(inv.seller_address),
        buyer: dash(inv.buyer_name),
        tin: dash(inv.seller_tin),
        lines: (inv.line_items || []).map(function (item) {
          return {
            description: dash(item.description),
            quantity: dash(item.quantity),
            amount: formatMoney(item.amount, inv.currency)
          };
        })
      }
    };
  }

  // One plain sentence for each flag code of SPEC.md section 9. The server's Flag has no title
  // field, so the sentences live here, in the UI only. The landing page lists the same ones.
  var FLAG_SENTENCES = {
    "MISSING_REQUIRED": "A field this type of document needs is missing.",
    "TOTAL_MISMATCH": "The total does not equal the sales, VAT and any discount added up.",
    "VAT_MISMATCH": "The VAT is not 12% of the vatable sales.",
    "LINE_ITEMS_MISMATCH": "The line items do not add up to the document totals.",
    "LINE_ITEM_MATH": "A line quantity times its unit price does not equal its amount.",
    "DUPLICATE": "The same invoice number from the same seller appears earlier in this batch.",
    "POSSIBLE_DUPLICATE": "Another invoice has the same seller, date and total.",
    "TIN_FORMAT": "The seller TIN does not have 9, 12 or 14 digits.",
    "FUTURE_DATE": "The invoice date is in the future.",
    "OLD_DATE": "The invoice date is more than 5 years ago.",
    "DUE_BEFORE_ISSUE": "The due date is before the invoice date.",
    "NEGATIVE_AMOUNT": "An amount on the document is negative.",
    "LOW_CONFIDENCE": "The reader was unsure about some fields. Please check them.",
    "INCOMPLETE_DOCUMENT": "The document looks cut off or incomplete.",
    "SPLIT_ACROSS_CHUNKS": "The document was split between two reads and may be incomplete."
  };

  window.Resibo = {
    el: el,
    clear: clear,
    plainNumber: plainNumber,
    formatMoney: formatMoney,
    dash: dash,
    formatPages: formatPages,
    plural: plural,
    formatManilaDate: formatManilaDate,
    totalLines: totalLines,
    slipModel: slipModel,
    FLAG_SENTENCES: FLAG_SENTENCES,
  };
})();
