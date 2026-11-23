# Resibo + n8n: Drive to Sheets

A PDF dropped into the Google Drive folder `Invoices/Inbox` is sent to Resibo, its invoices
are appended to a Google Sheet, a summary email is sent, and the file is moved to
`Invoices/Processed` or `Invoices/Needs Review`.

> **Status:** the workflow file was written against n8n 2.48 and checked structurally only
> (it parses, has the nodes and both branches, carries no credentials). It has **not** been run:
> that needs your Google accounts. Import it, follow the steps below, and fix any node that n8n
> flags for a newer node version.

![Workflow canvas](TODO-add-screenshot.png)

*TODO (human step 11): replace the image above with a screenshot of the imported canvas.*

## What the workflow does

1. **Google Drive Trigger**: event "File Created" in `Invoices/Inbox`, polled every minute.
   Upload files into the folder. Files that were *moved* into it may not count as created.
2. **Set: config**: four values you edit once: `resibo_url`, `public_url`, `notify_email`,
   `sheet_id`.
3. **IF: is PDF**: anything that is not `application/pdf` is moved to `Invoices/Needs Review`.
4. **Google Drive: Download file**: binary property `data`.
5. **HTTP Request: Resibo extract**: `POST {resibo_url}/api/extract`, multipart field `file`,
   Header Auth credential, timeout 600 s, no automatic retry (a retry after a timeout would pay
   for the whole extraction again), *On Error: Continue (using error output)*.
   - Error output: **Gmail: Resibo failed** emails the error, then **Google Drive: Move (failed)**
     moves the file to `Invoices/Needs Review`.
   - Branch A, per invoice: **Split Out: invoices**, **Set: map columns**,
     **Google Sheets: Append invoices**.
   - Branch B, once per file: **Code: build summary**, **Gmail: Send summary**, then
     **IF: documents == 0 or any REVIEW/ERROR**, which moves the file to
     `Invoices/Needs Review` or `Invoices/Processed`. A PDF with zero invoices still sends an
     email and is moved, because Branch B does not depend on Split Out.

Every node after node 5 reads the Drive file through `$('Google Drive Trigger').first().json`
(`id`, `name`, `webViewLink`), never through `$json` pairing.

The workflow reads these fields of the Resibo batch JSON (the frozen response shape of
`POST /api/extract`): `batch_id`, `invoices[]` (`id`, `status`, `flags[].message`,
`seller_name`, `invoice_number`, `invoice_date`, `total_amount`, ...), `summary.documents`,
`summary.ok`, `summary.review`, `summary.error`, `summary.total_amount_by_currency`,
`downloads.xlsx`.

## 1. Run n8n

**Locally with Docker Compose** (n8n and Resibo on one network):

```bash
cp .env.example .env        # then set ANTHROPIC_API_KEY and RESIBO_API_KEY
docker compose up n8n resibo
```

n8n is at <http://localhost:5678>. In **Set: config** use `resibo_url = http://resibo:8000`
(inside Compose the app is reachable by service name). `public_url` is what a human clicks in
the email, so use `http://localhost:8000` locally.

**On n8n Cloud** (trial): create a workflow there. n8n Cloud cannot reach `http://resibo:8000`,
so deploy Resibo first (Railway) and use its URL for both `resibo_url` and `public_url`.

## 2. Import

Workflows, then Import from file, then choose `n8n/resibo-drive-to-sheets.json`.

## 3. Credentials to create

| In n8n | Used by | Notes |
|---|---|---|
| **Header Auth** named `Resibo API key` | HTTP Request: Resibo extract | Header name `X-API-Key`, value = the `RESIBO_API_KEY` set on the Resibo server. Keyed requests skip the public limits. |
| **Google Drive OAuth2** | Trigger, download and the Move nodes | Create an OAuth client in Google Cloud (Drive API enabled), add your Google account as a test user. |
| **Google Sheets OAuth2** | Append invoices | Same Google Cloud project, Sheets API enabled. |
| **Gmail OAuth2** | Both Gmail nodes | Same project, Gmail API enabled. |

The exported file contains only credential *names*, no secrets. After import, open each node
that shows a credential warning and pick the credential you created.

## 4. Drive folders and the Sheet

1. In Google Drive create `Invoices/Inbox`, `Invoices/Processed` and `Invoices/Needs Review`.
2. In **Google Drive Trigger**, choose `Inbox` as the folder to watch.
3. In each **Google Drive: Move** node, choose the matching destination folder.
4. Create a Google Sheet named "Resibo Invoices" with a tab named `Invoices` and this header
   row (the same columns as the Resibo Excel export, plus four):
   `ID, Status, Flags, Document Type, Seller, Seller TIN, Buyer, Buyer TIN, Invoice No., Invoice Date, Due Date, Currency, VATable Sales, VAT-Exempt, Zero-Rated, Subtotal, VAT, Discount, Withholding Tax, Total, Payment Terms, Payment Method, Pages, Uncertain Fields, Notes, Source File, Drive Link, Batch ID, Processed At`
5. Copy the Sheet id (the long string in its URL) into **Set: config** as `sheet_id`, and set
   `notify_email`.

## 5. Test

1. Activate the workflow (or use "Execute workflow" and upload a file).
2. Upload a PDF, for example `samples/sample-invoices.pdf`, into `Invoices/Inbox`.
3. Within about a minute you should see: rows in the Sheet, a summary email such as
   `Resibo: 8 documents processed, 2 need review - sample-invoices.pdf`, and the file moved to
   `Invoices/Needs Review` (it has flagged documents).
4. Upload a text file renamed `.pdf`: Resibo returns `NOT_A_PDF`, you get the failure email, and
   the file is moved to `Invoices/Needs Review`.

## Optional extras (not in the exported file)

- Split Out `line_items` and append them to a "Line Items" tab.
- An **Error Workflow** with an Error Trigger node as a last-resort alert. It can only email: its
  payload does not carry the Drive file id, so it cannot move the file.

## Notes

- Excel download links in the email are valid for 60 minutes.
- Resibo runs chunks one after another; a long PDF can take minutes. The HTTP node waits up to
  600 s. Check your host's maximum request duration against your longest file and lower
  `MAX_PAGES` if needed.
