# API contract

Use `sapo_read`; resource names: check, products, orders, customers, variants, locations. Control owns fixed HTTPS destinations, authentication, bounded responses and safe error codes. The model cannot supply a URL, HTTP method, credential, or authorization header. KiotViet access tokens are cached encrypted and renewed before expiry.
