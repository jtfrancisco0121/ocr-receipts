"""JSON schema for the structured output (SPEC.md section 8.7). Fixed, never generated."""

_LINE_ITEM_FIELDS = ["description", "quantity", "unit", "unit_price", "amount"]

_INVOICE_REQUIRED = [
    "document_type",
    "seller_name",
    "seller_tin",
    "seller_address",
    "buyer_name",
    "buyer_tin",
    "buyer_address",
    "invoice_number",
    "invoice_date",
    "due_date",
    "payment_terms",
    "payment_method",
    "currency",
    "vatable_sales",
    "vat_exempt_sales",
    "zero_rated_sales",
    "subtotal",
    "vat_amount",
    "discount",
    "withholding_tax",
    "total_amount",
    "line_items",
    "source_pages",
    "is_complete",
    "uncertain_fields",
    "extraction_notes",
]

RECORD_INVOICES_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["invoices"],
    "properties": {
        "invoices": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": _INVOICE_REQUIRED,
                "properties": {
                    "document_type": {
                        "type": "string",
                        "enum": [
                            "sales_invoice",
                            "service_invoice",
                            "official_receipt",
                            "cash_receipt",
                            "delivery_receipt",
                            "billing_statement",
                            "other",
                        ],
                    },
                    "seller_name": {"type": "string"},
                    "seller_tin": {"type": "string"},
                    "seller_address": {"type": "string"},
                    "buyer_name": {"type": "string"},
                    "buyer_tin": {"type": "string"},
                    "buyer_address": {"type": "string"},
                    "invoice_number": {"type": "string"},
                    "invoice_date": {
                        "type": "string",
                        "description": "YYYY-MM-DD, or empty string",
                    },
                    "due_date": {"type": "string", "description": "YYYY-MM-DD, or empty string"},
                    "payment_terms": {"type": "string"},
                    "payment_method": {"type": "string"},
                    "currency": {
                        "type": "string",
                        "description": "ISO 4217 code; PHP if a peso sign or no currency is shown",
                    },
                    "vatable_sales": {
                        "type": "string",
                        "description": "Decimal amount like 11200.00, or empty string",
                    },
                    "vat_exempt_sales": {"type": "string"},
                    "zero_rated_sales": {"type": "string"},
                    "subtotal": {"type": "string"},
                    "vat_amount": {"type": "string"},
                    "discount": {"type": "string"},
                    "withholding_tax": {"type": "string"},
                    "total_amount": {"type": "string"},
                    "line_items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": _LINE_ITEM_FIELDS,
                            "properties": {
                                "description": {"type": "string"},
                                "quantity": {"type": "string"},
                                "unit": {"type": "string"},
                                "unit_price": {"type": "string"},
                                "amount": {"type": "string"},
                            },
                        },
                    },
                    "source_pages": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "1-based page numbers in the ORIGINAL file; at least one",
                    },
                    "is_complete": {"type": "boolean"},
                    "uncertain_fields": {"type": "array", "items": {"type": "string"}},
                    "extraction_notes": {"type": "string"},
                },
            },
        }
    },
}
