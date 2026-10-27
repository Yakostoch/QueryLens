-- Тестовые данные для отдельной базы; скрипт управляет транзакцией и откатом.
CREATE SCHEMA querylens_lab;
CREATE TABLE querylens_lab.users (
 id integer PRIMARY KEY, email text, city_id integer NOT NULL, active boolean NOT NULL,
 created_at timestamptz NOT NULL, profile jsonb
);
CREATE TABLE querylens_lab.products (
 id integer PRIMARY KEY, name text NOT NULL, category_id integer NOT NULL
);
CREATE TABLE querylens_lab.orders (
 id integer PRIMARY KEY, user_id integer REFERENCES querylens_lab.users(id),
 product_id integer REFERENCES querylens_lab.products(id), category_id integer NOT NULL,
 total numeric(12,2) NOT NULL CHECK(total>=0), created_at timestamptz NOT NULL,
 external_code text
);
INSERT INTO querylens_lab.users
 SELECT g, CASE WHEN g%17=0 THEN NULL ELSE 'user'||g||'@example.com' END,
        g%10, g%2=0, '2024-01-01'::timestamptz+g*interval '1 hour', '{"test":true}'::jsonb
 FROM generate_series(1,200) g;
INSERT INTO querylens_lab.products
 SELECT g, CASE WHEN g%20=0 THEN 'phone model '||g ELSE 'product '||g END, g%10
 FROM generate_series(1,5000) g;
INSERT INTO querylens_lab.orders
 SELECT g, CASE WHEN g%19=0 THEN NULL ELSE 1+g%200 END, 1+g%5000, g%10,
        (g%1000)::numeric, '2024-01-01'::timestamptz+g*interval '1 hour', 'user'||(1+g%200)||'@example.com'
 FROM generate_series(1,1000) g;
CREATE INDEX users_email_idx ON querylens_lab.users(email);
CREATE INDEX users_lower_email_idx ON querylens_lab.users(lower(email));
CREATE INDEX users_partial_lower_email_idx ON querylens_lab.users(lower(email)) WHERE active;
CREATE INDEX orders_user_idx ON querylens_lab.orders(user_id);
CREATE INDEX orders_order_idx ON querylens_lab.orders(created_at DESC,id DESC);
CREATE INDEX products_name_idx ON querylens_lab.products(name);
CREATE INDEX products_lower_name_idx ON querylens_lab.products(lower(name));
ANALYZE querylens_lab.users;
ANALYZE querylens_lab.products;
ANALYZE querylens_lab.orders;
-- Дополнительные схемы для проверки независимости от имён объектов.
CREATE SCHEMA "QL Tenant A";
CREATE SCHEMA "QL Tenant B";
CREATE TABLE "QL Tenant A"."Records" ("Key" integer PRIMARY KEY,"Label" text NOT NULL);
CREATE TABLE "QL Tenant B"."Records" ("Key" integer PRIMARY KEY,"Label" text NOT NULL);
CREATE TABLE "QL Tenant B"."Связи" ("Источник" integer,"Описание" text);
INSERT INTO "QL Tenant A"."Records" SELECT g,'label '||g FROM generate_series(1,20) g;
INSERT INTO "QL Tenant B"."Records" SELECT g,'other '||g FROM generate_series(1,20) g;
INSERT INTO "QL Tenant B"."Связи" SELECT g,'note '||g FROM generate_series(1,20) g;
ANALYZE "QL Tenant A"."Records";
ANALYZE "QL Tenant B"."Records";
ANALYZE "QL Tenant B"."Связи";
-- Значения NULL и повторы для проверки семантики NOT IN.
CREATE TABLE querylens_lab.probe_nn (key integer NOT NULL, label text);
CREATE TABLE querylens_lab.probe_nullable (key integer, label text);
CREATE TABLE querylens_lab.deny_nn (key integer NOT NULL);
CREATE TABLE querylens_lab.deny_nullable (key integer);
INSERT INTO querylens_lab.probe_nn VALUES (1,'one'),(2,'two'),(2,'duplicate'),(3,'three');
INSERT INTO querylens_lab.probe_nullable VALUES (NULL,'null'),(1,'one'),(2,'two'),(2,'duplicate');
INSERT INTO querylens_lab.deny_nn VALUES (1),(1);
INSERT INTO querylens_lab.deny_nullable VALUES (1),(NULL);
ANALYZE querylens_lab.probe_nn;
ANALYZE querylens_lab.probe_nullable;
ANALYZE querylens_lab.deny_nn;
ANALYZE querylens_lab.deny_nullable;
-- Первичный ключ родителя не гарантирует уникальность во всех наследуемых таблицах.
CREATE TABLE querylens_lab.inherited_parent (id integer PRIMARY KEY);
CREATE TABLE querylens_lab.inherited_child () INHERITS (querylens_lab.inherited_parent);
INSERT INTO querylens_lab.inherited_parent VALUES (1);
INSERT INTO querylens_lab.inherited_child VALUES (1);
ANALYZE querylens_lab.inherited_parent;
ANALYZE querylens_lab.inherited_child;
