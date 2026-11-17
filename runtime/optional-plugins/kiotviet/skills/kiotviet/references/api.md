# KiotViet retail Public API

Source: https://www.kiotviet.vn/huong-dan-su-dung-kiotviet/retail-ket-noi-api/public-api/
Retrieved 2026-11-16; page updated 2026-10-20. The operation catalog covers 86
business requests (48 reads, 38 writes). OAuth is internal to Control.

## Calling convention

- Read: `kiotviet_read({"operation":"products.get","id":"123"})`.
- Filter/page: `kiotviet_read({"operation":"products.list","query":{"name":"Tea","pageSize":"20","currentItem":"0","includeInventory":"true"}})`.
- Write: `kiotviet_write({"operation":"products.create","body":{...}})`.
- IDs/codes and all query values are strings. Body values use native JSON types.
- Query names/case must match the table. Omit absent filters. `pageSize` defaults
  to 100 where supported, maximum 100; `currentItem` is an offset up to 1000000.
  Operations without documented pagination return one provider response, which
  must not be described as an exhaustive export.
- Request bodies are JSON objects, up to 256 KiB, nesting up to 20 and arrays up
  to 100 entries. Responses are capped at 2 MiB. Narrow the request when needed.
- Do not pass retailer, authentication, arbitrary URLs or headers. Access
  permissions belong to the saved connection; an operation being exposed does
  not establish that the store permits it.

## Operations

GET rows use `kiotviet_read`; other rows use `kiotviet_write`. POST/PUT require
`body`. DELETE normally has no body except `invoices.delete` and
`vouchers.cancel`. Supply `id` or `code` only when the path has that placeholder.

| Operation | Method/path | Section | Allowed query fields |
| --- | --- | --- | --- |
| `categories.list` | `GET /categories` | 2.3.1 | `lastModifiedFrom`, `pageSize`, `currentItem`, `orderBy`, `orderDirection`, `hierachicalData` |
| `categories.get` | `GET /categories/{id}` | 2.3.2 | — |
| `categories.create` | `POST /categories` | 2.3.3 | — |
| `categories.update` | `PUT /categories/{id}` | 2.3.4 | — |
| `categories.delete` | `DELETE /categories/{id}` | 2.3.5 | — |
| `products.list` | `GET /products` | 2.4.1 | `orderBy`, `lastModifiedFrom`, `pageSize`, `currentItem`, `includeInventory`, `includePricebook`, `IncludeSerials`, `IncludeBatchExpires`, `includeWarranties`, `masterUnitId`, `masterProductId`, `categoryId`, `BranchIds`, `orderDirection`, `includeRemoveIds`, `includeQuantity`, `productType`, `includeMaterial`, `isActive`, `name`, `includeSoftDeletedAttribute`, `tradeMarkId`, `IncludeProductShelves` |
| `products.get` | `GET /products/{id}` | 2.4.2 | `includeSoftDeletedAttribute` |
| `products.get_by_code` | `GET /products/code/{code}` | 2.4.2 | `includeSoftDeletedAttribute` |
| `products.create` | `POST /products` | 2.4.3 | — |
| `products.update` | `PUT /products/{id}` | 2.4.4 | `branchId` |
| `products.delete` | `DELETE /products/{id}` | 2.4.5 | — |
| `attributes.list` | `GET /attributes/allwithdistinctvalue` | 2.4.6 | — |
| `products.create_batch` | `POST /listaddproducts` | 2.4.7 | — |
| `products.update_batch` | `PUT /listupdatedproducts` | 2.4.8 | — |
| `inventory.list` | `GET /productOnHands` | 2.4.9 | `orderBy`, `lastModifiedFrom`, `branchIds`, `pageSize`, `currentItem` |
| `orders.list` | `GET /orders` | 2.5.1 | `branchIds`, `customerIds`, `customerCode`, `status`, `includePayment`, `includeOrderDelivery`, `lastModifiedFrom`, `pageSize`, `currentItem`, `toDate`, `orderBy`, `orderDirection`, `createdDate`, `saleChannelId` |
| `orders.get` | `GET /orders/{id}` | 2.5.2 | — |
| `orders.get_by_code` | `GET /orders/code/{code}` | 2.5.2 | — |
| `orders.create` | `POST /orders` | 2.5.3 | — |
| `orders.update` | `PUT /orders/{id}` | 2.5.4 | — |
| `orders.delete` | `DELETE /orders/{id}` | 2.5.5 | `IsVoidPayment` |
| `customers.list` | `GET /customers` | 2.6.1 | `code`, `name`, `contactNumber`, `lastModifiedFrom`, `pageSize`, `currentItem`, `orderBy`, `orderDirection`, `includeRemoveIds`, `includeTotal`, `includeCustomerGroup`, `birthDate`, `groupId`, `includeCustomerSocial` |
| `customers.get` | `GET /customers/{id}` | 2.6.2 | — |
| `customers.get_by_code` | `GET /customers/code/{code}` | 2.6.2 | — |
| `customers.create` | `POST /customers` | 2.6.3 | — |
| `customers.update` | `PUT /customers/{id}` | 2.6.4 | — |
| `customers.delete` | `DELETE /customers/{id}` | 2.6.5 | — |
| `customers.create_batch` | `POST /listaddcutomers` | 2.6.6 | — |
| `customers.update_batch` | `PUT /listupdatecustomers` | 2.6.7 | — |
| `branches.list` | `GET /branches` | 2.7 | `lastModifiedFrom`, `pageSize`, `currentItem`, `orderBy`, `orderDirection`, `includeRemoveIds` |
| `users.list` | `GET /users` | 2.8 | `lastModifiedFrom`, `pageSize`, `currentItem`, `orderBy`, `orderDirection`, `includeRemoveIds` |
| `bankaccounts.list` | `GET /BankAccounts` | 2.9 | `lastModifiedFrom`, `pageSize`, `currentItem`, `orderBy`, `orderDirection`, `includeRemoveIds` |
| `surcharges.list` | `GET /surchages` | 2.10.1 | `branchId`, `lastModifiedFrom`, `pageSize`, `currentItem`, `orderBy`, `orderDirection` |
| `surcharges.create` | `POST /surchages` | 2.10.2 | — |
| `surcharges.update` | `PUT /surchages/{id}` | 2.10.3 | — |
| `surcharges.set_active` | `POST /surchages/{id}/activesurchage` | 2.10.4 | — |
| `webhooks.create` | `POST /webhooks` | 2.11.1 | — |
| `webhooks.delete` | `DELETE /webhooks/{id}` | 2.11.2 | — |
| `webhooks.list` | `GET /webhooks` | 2.11.11 | — |
| `webhooks.get` | `GET /webhooks/{id}` | 2.11.12 | — |
| `invoices.list` | `GET /invoices` | 2.12.1 | `branchIds`, `customerIds`, `customerCode`, `status`, `includePayment`, `includeInvoiceDelivery`, `lastModifiedFrom`, `pageSize`, `currentItem`, `toDate`, `orderBy`, `orderDirection`, `orderId`, `createdDate`, `fromPurchaseDate`, `toPurchaseDate`, `IncludeSaleChannel` |
| `invoices.get` | `GET /invoices/{id}` | 2.12.2 | — |
| `invoices.get_by_code` | `GET /invoices/code/{code}` | 2.12.2 | — |
| `invoices.create` | `POST /invoices` | 2.12.3 | — |
| `invoices.update` | `PUT /invoices/{id}` | 2.12.4 | — |
| `invoices.delete` | `DELETE /invoices` | 2.12.5 | — |
| `customer_groups.list` | `GET /customers/group` | 2.13.1 | — |
| `cashflow.list` | `GET /cashflow` | 2.14.1 | `branchIds`, `code`, `userId`, `accountId`, `partnerType`, `method`, `cashFlowGroupId`, `usedForFinancialReporting`, `partnerName`, `contactNumber`, `isReceipt`, `includeAccount`, `includeBranch`, `includeUser`, `startDate`, `endDate`, `status`, `ids`, `pageSize`, `currentItem` |
| `payments.create` | `POST /payments` | 2.14.2 | — |
| `purchaseorders.list` | `GET /purchaseorders` | 2.15.1 | `branchIds`, `status`, `includePayment`, `includeOrderDelivery`, `fromPurchaseDate`, `toPurchaseDate`, `pageSize`, `currentItem`, `TaxIds` |
| `purchaseorders.get` | `GET /purchaseorders/{id}` | 2.15.2 | — |
| `purchaseorders.create` | `POST /purchaseorders` | 2.15.3 | — |
| `purchaseorders.update` | `PUT /purchaseorders/{id}` | 2.15.4 | — |
| `purchaseorders.delete` | `DELETE /purchaseorders` | 2.15.5 | `id`, `IsVoidPayment` |
| `transfers.list` | `GET /transfers` | 2.16.1 | `toBranchIds`, `fromBranchIds`, `status`, `pageSize`, `currentItem`, `fromReceivedDate`, `toReceivedDate`, `fromTransferDate`, `toTransferDate` |
| `transfers.get` | `GET /transfers/{id}` | 2.16.2 | — |
| `transfers.get_by_code` | `GET /transfers/code/{code}` | 2.16.2 | — |
| `transfers.create` | `POST /transfers` | 2.16.3 | — |
| `transfers.update` | `PUT /transfers/{id}` | 2.16.4 | — |
| `transfers.delete` | `DELETE /transfers/{id}` | 2.16.5 | — |
| `pricebooks.list` | `GET /pricebooks` | 2.17.1 | `includePriceBookBranch`, `includePriceBookCustomerGroups`, `includePriceBookUsers`, `orderBy`, `orderDirection`, `currentItem`, `pageSize`, `lastModifiedFrom` |
| `pricebooks.get` | `GET /pricebooks/{id}` | 2.17.2 | `orderBy`, `orderDirection`, `currentItem`, `pageSize`, `lastModifiedFrom` |
| `pricebooks.update_detail` | `POST /pricebooks/detail` | 2.17.3 | — |
| `salechannels.list` | `GET /salechannel` | 2.18.1 | `orderBy`, `orderDirection`, `currentItem`, `pageSize`, `lastModifiedFrom` |
| `returns.list` | `GET /returns` | 2.19.1 | `orderBy`, `lastModifiedFrom`, `pageSize`, `currentItem`, `includePayment`, `orderDirection` |
| `returns.get` | `GET /returns/{id}` | 2.19.2 | — |
| `returns.get_by_code` | `GET /returns/code/{code}` | 2.19.2 | — |
| `ordersuppliers.list` | `GET /ordersuppliers` | 2.20.1 | `branchId`, `status`, `productKey`, `supplierKey`, `userNamKey`, `userNamCreatedKey`, `expensesOthersIds`, `descriptionKey`, `codeKey`, `purchaseOrderCode`, `pageSize`, `currentItem` |
| `ordersuppliers.get` | `GET /ordersuppliers/{id}` | 2.20.2 | — |
| `locations.list` | `GET /locations` | 2.21 | — |
| `settings.get` | `GET /settings` | 2.22 | — |
| `coupons.set_used` | `POST /coupons/setused` | 2.23 | — |
| `voucher_campaigns.list` | `GET /vouchercampaign` | 2.24.1 | `includeVoucherBranchs`, `includeVoucherUsers`, `isActive`, `id`, `isGlobal`, `forAllCusGroup`, `forAllUser` |
| `vouchers.list` | `GET /voucher` | 2.24.2 | `campaignId`, `status`, `lastModifiedFrom`, `code` |
| `vouchers.create` | `POST /voucher` | 2.24.3 | — |
| `vouchers.release` | `POST /voucher/release/give` | 2.24.4 | — |
| `vouchers.cancel` | `DELETE /voucher/cancel` | 2.24.5 | — |
| `trademarks.list` | `GET /trademark` | 2.25.1 | `lastModifiedFrom`, `pageSize`, `currentItem`, `orderBy`, `orderDirection` |
| `suppliers.list` | `GET /suppliers` | 2.26.1 | `pageSize`, `currentItem`, `orderDirection`, `code`, `name`, `contactNumber`, `lastModifiedFrom`, `includeRemoveIds`, `includeTotal`, `includeSupplierGroup` |
| `suppliers.get` | `GET /suppliers/{id}` | 2.26.2 | — |
| `suppliers.get_by_code` | `GET /suppliers/{code}` | 2.26.2 | — |
| `taxes.list` | `GET /tax/detail` | 2.27.1 | — |
| `einvoices.update_info` | `PUT /einvoices/info` | 2.28.1 | — |
| `qr.accounts` | `GET /qr-payment/accounts` | 2.29.1 | `isRegisterQrNoti`, `branchId` |
| `qr.generate` | `POST /qr-payment/generate` | 2.29.2 | — |
| `qr.status` | `GET /qr-payment/status` | 2.29.3 | `qrId` |

## Payload recipes

These are synthetic examples. Resolve actual IDs/codes and user intent first;
read the linked official section for advanced taxes, variants, serial/lot data,
customer e-invoice fields, delivery, discounts and accounting options.

### Products and stock

Create an ordinary product (replace all example values):

```json
{"operation":"products.create","body":{"name":"Example tea","code":"DEMO-TEA","categoryId":123,"allowsSale":true,"unit":"box","basePrice":50000,"inventories":[{"branchId":456,"onHand":10,"cost":30000}]}}
```

`onHand` is absolute stock at a branch. Receiving additional units is a separate
purchase receipt; do not set stock to the received quantity by mistake.
`products.update` takes top-level `id`, optional `query.branchId`, and the native
product fields in `body`. `products.create_batch` / `products.update_batch` use
`{"listProducts":[...]}`; updates identify each product with its `id`.
Categories use `categoryName` and optional `parentId` in their body.

### Purchases, transfers and sales

```json
{"operation":"purchaseorders.create","body":{"branchId":456,"purchaseDate":"2026-11-16T10:00:00+07:00","supplier":{"code":"NCC-DEMO","name":"Example supplier"},"isDraft":1,"paidAmount":0,"purchaseOrderDetails":[{"productCode":"DEMO-TEA","quantity":5,"price":30000}]}}
```

The example creates a draft. Choose draft/completed behavior deliberately using
the documented `isDraft` value; verify resulting status and stock. Supplier data
can create a supplier as part of a purchase: reuse existing codes when applicable.
Transfers use `fromBranchId`, `toBranchId`, `isDraft`, `transferDetails` with
`productCode`/`productId`, `sendQuantity`, `recivedQuantity`, `price` as documented.
Do not silently correct native field spellings.
Orders and invoices use `branchId`, `purchaseDate`, customer information and
`orderDetails` or `invoiceDetails` containing product IDs, quantity and price.
Payment/delivery/tax fields can change accounting; supply them only as requested.
`payments.create` takes `invoiceId`, `amount`, `method` and optional `accountId`.
`pricebooks.update_detail` takes `pricebookId`, `productId`, `price` (0 identifies
the default price book).

### Customers and surcharges

Customer create/update bodies support `code`, `name`, contact/address and the
other fields documented in 2.6.3/2.6.4. Batch operations use `listCustomers` and
updates require each record's `id`. Surcharges use the native fields from 2.10;
`surcharges.set_active` takes top-level `id` and `{"isActive":false}` or true.

### Delete / cancel behavior

- `orders.delete`: top-level `id`, optional `query.IsVoidPayment` string boolean.
- `purchaseorders.delete`: **query** `id` (required), optional `IsVoidPayment`.
- `invoices.delete`: **body** `{"id":123,"isVoidPayment":false}`.
- `vouchers.cancel`: **body** `{"CampaignId":123,"Vouchers":[{"Code":"DEMO"}]}`.
- Other DELETE operations use their top-level `id` and no body.

Do not enable payment cancellation without user authorization. KiotViet can
reject deletes based on document state or references.

### Coupons, vouchers and e-invoices

- `coupons.set_used`: `{"coupons":[{"code":"DEMO"}]}`.
- `vouchers.list` requires `query.campaignId`; status/code filters are optional.
- `vouchers.create`: `{"voucherCampaignId":123,"data":[{"code":"DEMO"}]}`.
- `vouchers.release`: `{"CampaignId":123,"Vouchers":[{"Code":"DEMO","ReleaseDate":"2026-11-16T10:00:00+07:00"}]}`.
- `einvoices.update_info`: `{"data":[{"invoiceId":123,"invoiceRefId":"<UUID>","partner":3,"status":2,"type":1,...}]}`. This updates external issuance metadata; it cannot modify invoices already issued by KiotViet. Never invent fiscal status or identifiers.

### Webhook subscriptions

```json
{"operation":"webhooks.create","body":{"Webhook":{"Type":"stock.update","Url":"https://receiver.example.com/kiotviet","IsActive":true,"Description":"Inventory updates"}}}
```

The receiver must be specified by the user. `Secret` is optional in the native
Webhook object; coordinate it securely with the receiver rather than asking for
credentials in chat. The official guide describes a Base64-encoded secret and
HMAC SHA-256 of the raw callback body via `X-Hub-Signature`. The receiver must
validate signatures, deduplicate event IDs and implement its own processing.
Customer/product/stock/order/invoice/pricebook/category/branch/QR event payloads
in section 2.11 are incoming notifications, not outbound HTTP operations.

### QR payments

Read `qr.accounts`, select a valid account for the requested branch, then use
`qr.generate` with `branchId`, `bankCode`, `accountNumber`, `amount`, `orderCode`,
`qrType` (1 invoice / 2 debt), optional `description` and `documentId`.
Amount must be positive and at most 10000000000. Generate `orderCode` once for
the intended action. The upstream does not deduplicate generation requests.
Read `qr.status` using required `query.qrId`; statuses are PENDING, PARTIAL,
PAID. Payment status is based on KiotViet notifications, not a bank query.

## Known documentation ambiguities

Preserve declared endpoint spellings: `/listaddcutomers`, `/surchages`,
`/activesurchage`, `/listupdatedproducts`. Supplier lookup by code is documented
as `/suppliers/{code}`. Voucher cancel declares DELETE despite a later POST
sentence. E-invoice metadata declares PUT despite an unrelated GET sentence.
This adapter uses the declared method and does not try mutation alternatives.
Only actual provider responses can validate these variants for a given store.

## Outcome handling

`provider_invalid_request`, `provider_forbidden`, `provider_record_not_found`,
`provider_conflict`, `provider_rate_limited` are distinct safe errors.
`provider_partial_failure` requires inspecting affected records before any
remaining writes. `provider_write_outcome_unknown` means a request may have
committed; never automatically repeat it. Read back before deciding next steps.
