-- =====================================================================================================================
-- OWNER-SUPPLIED REFERENCE DATA.  >>> PLACEHOLDER CONTENT <<<  (owner, 13 October 2026)
--
-- The rows below are Provisioning's prototype demo-shop data (its 00004_reference_data.sql), with the business type
-- renamed to the *_shop form and the same fixed IDs, so that the demo clothing shop keeps working on Kind. The owner
-- replaces or extends them with the real business types, product types and product type schemas.
--
-- Editing rules (Flyway re-runs this file whenever it changes; every statement is an upsert):
--   * never delete a row an environment has applied: set is_active = false;
--   * never change an ID, nor a schema's product_type_id, version_number or field_schema (the immutability trigger
--     fails the deployment): add a new schema row with the next version_number instead;
--   * business type codes match ^[a-z][a-z0-9_]*_shop$; product type codes match ^[a-z][a-z0-9_]*$;
--   * every field_schema is a JSON Schema 2020-12 object schema; every active product type has an active schema.
-- =====================================================================================================================
DO $$
BEGIN
  IF current_user <> 'catalog_owner' THEN
    RAISE EXCEPTION 'catalog migrations must run as role catalog_owner (current role: %)', current_user
      USING ERRCODE = '42501';
  END IF;
END
$$;

INSERT INTO catalog.business_types (business_type_id, code, name, is_active, created_at) VALUES
  ('3f555430-6a1f-405b-b70a-68dc36c1f680', 'clothing_shop', 'Clothes', true, now()),
  ('5380c9e3-5df5-48f2-93c5-e5cafeeb5f25', 'food_shop', 'Foods', true, now()),
  ('bd6be228-764d-4076-b773-b3dcba860a65', 'beauty_shop', 'Beauty', true, now()),
  ('7a9a4f70-bb92-4113-a6ae-d0981428f68a', 'furniture_shop', 'Furniture', true, now()),
  ('610e21be-6af8-4c0f-9057-3a4e33e09765', 'tech_shop', 'Technology', true, now())
ON CONFLICT (business_type_id) DO UPDATE
  SET code = EXCLUDED.code, name = EXCLUDED.name, is_active = EXCLUDED.is_active;

INSERT INTO catalog.product_types (product_type_id, business_type_id, code, name, is_active, created_at) VALUES
  ('a9693e48-969a-4f26-984c-d30d931bae1f', '3f555430-6a1f-405b-b70a-68dc36c1f680', 'top', 'Top', true, now()),
  ('4be55fd8-aca9-4d96-a5f6-6252d4a5b24e', '3f555430-6a1f-405b-b70a-68dc36c1f680', 'outerwear', 'Outerwear', true, now()),
  ('a2710129-db86-4f62-8190-5414274d38db', '3f555430-6a1f-405b-b70a-68dc36c1f680', 'trousers', 'Trousers', true, now()),
  ('cefc6f84-d8b3-4718-b43d-0e39612dffc7', '3f555430-6a1f-405b-b70a-68dc36c1f680', 'accessory', 'Accessory', true, now()),
  ('1617e841-f602-4ccd-b9ec-0f9dfc9afbf8', '5380c9e3-5df5-48f2-93c5-e5cafeeb5f25', 'menu_item', 'Menu item', true, now()),
  ('01eb1184-c258-423a-9ba2-d8c4be996c27', 'bd6be228-764d-4076-b773-b3dcba860a65', 'beauty_product', 'Beauty product', true, now()),
  ('b825a05f-b200-4d29-baab-a4fbf36b4902', '7a9a4f70-bb92-4113-a6ae-d0981428f68a', 'furniture_item', 'Furniture item', true, now()),
  ('4b7b261e-a4cb-44d2-b7ee-609b458911cd', '610e21be-6af8-4c0f-9057-3a4e33e09765', 'tech_device', 'Device', true, now())
ON CONFLICT (product_type_id) DO UPDATE
  SET code = EXCLUDED.code, name = EXCLUDED.name, is_active = EXCLUDED.is_active;

INSERT INTO catalog.product_type_schemas
  (product_type_schema_id, product_type_id, version_number, field_schema, is_active, created_at)
SELECT s.id::uuid, s.product_type_id::uuid, 1, f.field_schema, true, now()
FROM (VALUES
        ('b7dc359b-5fed-4b93-b7ab-bf493df30ec2', 'a9693e48-969a-4f26-984c-d30d931bae1f', 'top'),
        ('d77a3003-a5a4-4582-8fe8-b91fb94233d0', '4be55fd8-aca9-4d96-a5f6-6252d4a5b24e', 'top'),
        ('1083801e-6d91-48ff-bcce-1326816c828f', 'a2710129-db86-4f62-8190-5414274d38db', 'trousers'),
        ('84b18ea8-cdb5-44b7-8f4c-e02baa393e27', 'cefc6f84-d8b3-4718-b43d-0e39612dffc7', 'accessory'),
        ('ea94d4d1-e2c0-45f0-a7ab-0f8dfbcd0a5c', '1617e841-f602-4ccd-b9ec-0f9dfc9afbf8', 'menu_item'),
        ('7c9751c0-4ad3-44c8-9115-d1deef0c5f08', '01eb1184-c258-423a-9ba2-d8c4be996c27', 'beauty_product'),
        ('e1b3fe0a-cc81-4f49-8736-3f35f6003f39', 'b825a05f-b200-4d29-baab-a4fbf36b4902', 'furniture_item'),
        ('a31408dd-d5d6-4fbf-b106-6ffc6bf77b14', '4b7b261e-a4cb-44d2-b7ee-609b458911cd', 'tech_device')
     ) AS s (id, product_type_id, kind)
JOIN (VALUES
        ('top', '{"type": "object", "properties": {
            "size": {"type": "array", "items": {"enum": ["XS", "S", "M", "L", "XL", "XXL"]}},
            "color": {"type": "array", "items": {"type": "string"}},
            "material": {"type": "string"},
            "fit": {"enum": ["slim", "regular", "relaxed", "oversized"]},
            "care": {"type": "string"}}, "additionalProperties": false}'::jsonb),
        ('trousers', '{"type": "object", "properties": {
            "size": {"type": "array", "items": {"enum": ["26", "28", "30", "32", "34", "36", "38", "40"]}},
            "color": {"type": "array", "items": {"type": "string"}},
            "material": {"type": "string"},
            "fit": {"enum": ["slim", "regular", "relaxed", "oversized"]},
            "care": {"type": "string"}}, "additionalProperties": false}'::jsonb),
        ('accessory', '{"type": "object", "properties": {
            "size": {"type": "array", "items": {"enum": ["One size"]}},
            "color": {"type": "array", "items": {"type": "string"}},
            "material": {"type": "string"},
            "care": {"type": "string"}}, "additionalProperties": false}'::jsonb),
        ('menu_item', '{"type": "object", "properties": {
            "portion": {"type": "string", "maxLength": 100},
            "spice_level": {"enum": ["none", "mild", "medium", "hot"]},
            "dietary": {"type": "array", "items": {"enum": ["vegetarian", "vegan", "halal", "gluten_free"]}}},
            "additionalProperties": false}'::jsonb),
        ('beauty_product', '{"type": "object", "properties": {
            "volume": {"type": "string", "maxLength": 100},
            "skin_type": {"type": "array", "items": {"enum": ["normal", "dry", "oily", "combination", "sensitive"]}},
            "ingredients": {"type": "string", "maxLength": 2000}}, "additionalProperties": false}'::jsonb),
        ('furniture_item', '{"type": "object", "properties": {
            "material": {"type": "string", "maxLength": 100},
            "dimensions": {"type": "string", "maxLength": 100},
            "color": {"type": "string", "maxLength": 100}}, "additionalProperties": false}'::jsonb),
        ('tech_device', '{"type": "object", "properties": {
            "brand": {"type": "string", "maxLength": 100},
            "model": {"type": "string", "maxLength": 100},
            "storage": {"type": "string", "maxLength": 100},
            "color": {"type": "string", "maxLength": 100},
            "warranty_months": {"type": "integer", "minimum": 0}}, "additionalProperties": false}'::jsonb)
     ) AS f (kind, field_schema) ON f.kind = s.kind
ON CONFLICT (product_type_schema_id) DO UPDATE
  SET product_type_id = EXCLUDED.product_type_id, version_number = EXCLUDED.version_number,
      field_schema = EXCLUDED.field_schema, is_active = EXCLUDED.is_active;
