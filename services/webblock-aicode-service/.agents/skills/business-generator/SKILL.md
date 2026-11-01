---
name: business-generator
description: Use this skill when modifying or adding business categories, Drizzle schema templates, or Puck component layout mapping for the onboarding generator.
---

# Business Onboarding & Drizzle Generator Skill

This skill explains how to customize business categories, Drizzle ORM schemas, and Puck component layouts.

## Architecture

The generator consists of three files:
1. `app/schemas/business.py`: Pydantic input models and validation.
2. `app/services/drizzle_generator.py`: Generates TypeScript Drizzle ORM code for `db/schema.ts`.
3. `app/services/business_layout.py`: Converts business details into pre-filled Puck visual components.

## Adding a New Business Domain

To add a new business category (e.g. `fitness_gym` or `real_estate`):

### 1. Update `BusinessType` in `app/schemas/business.py`
```python
class BusinessType(str, Enum):
    ...
    FITNESS = "fitness"
```

### 2. Add Domain Tables in `app/services/drizzle_generator.py`
Add conditions for the new category:
```python
if "fitness" in b_type or "gym" in b_type:
    lines.extend([
        'export const classes = pgTable("classes", {',
        '  id: serial("id").primaryKey(),',
        '  name: text("name").notNull(),',
        '  instructor: text("instructor"),',
        '  scheduleTime: text("schedule_time"),',
        '  capacity: integer("capacity").default(20),',
        '});',
    ])
```

A new table must also exist in the shared `site_data` schema, or the site builds and then fails at runtime: add a migration `sql/00N_<name>.sql` (plus its Alembic revision) that creates it as `sql/006_site_data.sql` does, with `project_id`, `UNIQUE (project_id, id)`, row-level security and grants to `webblock_site`.

### 3. Add Sample Offerings in `app/services/business_layout.py`
In `_generate_sample_items(b_type)`:
```python
if "fitness" in b_type_lower or "gym" in b_type_lower:
    return [
        {"name": "Unlimited Monthly Pass", "price": "$79 / mo", "description": "Access to all studio classes and weight room."},
        {"name": "1-on-1 Personal Training", "price": "$60 / session", "description": "Customized fitness and nutrition coaching."},
    ]
```

## Testing

Run tests to verify catalog validity and schema generation:

```bash
pytest tests/test_business_onboarding.py
pytest tests/test_onboarding_routes.py
pytest tests/test_site_data.py
```
