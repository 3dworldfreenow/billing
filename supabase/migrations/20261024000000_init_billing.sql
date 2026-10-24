-- Billing app schema. Mirrors billing/models.py; IF NOT EXISTS so it is safe
-- whether this migration or the app's db.create_all() runs first.

CREATE TABLE IF NOT EXISTS customer (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	email VARCHAR(120), 
	phone VARCHAR(40), 
	address TEXT, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS product (
	id SERIAL NOT NULL, 
	name VARCHAR(120) NOT NULL, 
	barcode VARCHAR(64), 
	unit_price NUMERIC(12, 2) NOT NULL, 
	PRIMARY KEY (id)
);

CREATE UNIQUE INDEX IF NOT EXISTS ix_product_barcode ON product (barcode);

CREATE TABLE IF NOT EXISTS invoice (
	id SERIAL NOT NULL, 
	customer_id INTEGER NOT NULL, 
	issue_date DATE NOT NULL, 
	due_date DATE NOT NULL, 
	tax_rate NUMERIC(5, 2) NOT NULL, 
	status VARCHAR(10) NOT NULL, 
	notes TEXT, 
	PRIMARY KEY (id), 
	FOREIGN KEY(customer_id) REFERENCES customer (id)
);

CREATE TABLE IF NOT EXISTS invoice_item (
	id SERIAL NOT NULL, 
	invoice_id INTEGER NOT NULL, 
	product_id INTEGER, 
	description VARCHAR(200) NOT NULL, 
	quantity NUMERIC(10, 2) NOT NULL, 
	unit_price NUMERIC(12, 2) NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(invoice_id) REFERENCES invoice (id), 
	FOREIGN KEY(product_id) REFERENCES product (id)
);

-- Block access through Supabase's Data API (anon/authenticated keys).
-- The Flask app connects as the table owner, which bypasses RLS.

ALTER TABLE "customer" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "product" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "invoice" ENABLE ROW LEVEL SECURITY;
ALTER TABLE "invoice_item" ENABLE ROW LEVEL SECURITY;
