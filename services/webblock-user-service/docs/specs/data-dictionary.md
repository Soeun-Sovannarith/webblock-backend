# WebBlock — Data Dictionary (by service)

> Source: `Data Dictionary.docx` + ERD `erd-v8.drawio` (service pages). One database (or schema) per service. A service **never** reads or writes another service's tables — it calls that service's API or listens to its events. Cross-service IDs (e.g. `owner_user_id`) are plain UUID columns with **no** FK constraint.

Physical table name = service prefix + table name (as on the ERD service pages).

## account-service (Keycloak users)

### `account_users`  (dictionary name: `users`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| user_id | uuid |  | Y | PK |  |  | Client account ID. |
| identity_issuer | varchar | 255 | Y |  |  |  | Verified Keycloak sign-in address. |
| identity_subject | varchar | 255 | Y |  |  |  | User ID within this Keycloak sign-in service. |
| email | varchar | 320 | Y |  |  |  | Contact email address. |
| display_name | varchar | 255 |  |  |  |  | Optional client display name. |
| status | enum |  | Y |  |  |  | Values: ACTIVE, SUSPENDED. |
| status_version | bigint |  | Y |  |  |  | Number increases by 1 with each status change. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |
| last_login_at | timestamptz |  |  |  |  |  | Last successful sign-in. |

### `account_user_status_changes`  (dictionary name: `user_status_changes`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| user_status_change_id | uuid |  | Y | PK |  |  | Status change and event ID. |
| user_id | uuid |  | Y | FK |  | users | Account whose status changed. |
| status_version | bigint |  | Y |  |  |  | Number increases by 1 with each status change. |
| from_status | enum |  | Y |  |  |  | Values: ACTIVE, SUSPENDED. |
| to_status | enum |  | Y |  |  |  | Values: ACTIVE, SUSPENDED. |
| reason_code | enum |  |  |  |  |  | Values: ABUSE, FRAUD, OWNER_REQUEST, OTHER. |
| note | varchar | 500 |  |  |  |  | Private admin note. |
| changed_by | varchar | 255 |  |  |  |  | Admin Keycloak ID; blank for a system change. |
| changed_at | timestamptz |  | Y |  |  |  | Change time. |

## project-service

### `project_projects`  (dictionary name: `projects`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| project_id | uuid |  | Y | PK |  |  | Website project ID. |
| owner_user_id | uuid |  | Y |  |  | users | Project owner ID. |
| name | varchar | 255 | Y |  |  |  | Display name. |
| subdomain | varchar | 63 | Y |  |  |  | Visitor-facing sub-domain chosen at creation; unique; cannot change. |
| business_type_id | uuid |  | Y |  |  | business_types | Business type chosen at creation; cannot change. |
| edit_revision | bigint |  | Y |  | 0 |  | Number used to detect conflicting edits. |
| component_catalog_version | varchar | 100 | Y |  |  |  | Component list version used for generation. |
| repository_id | bigint |  |  |  |  |  | Gitea repository ID. |
| repository_full_name | varchar | 140 |  |  |  |  | Gitea organization and repository. |
| status | enum |  | Y |  |  |  | Values: DRAFT, ACTIVE, SUSPENDED. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |
| deleted_at | timestamptz |  |  |  |  |  | Deletion time; cannot be changed once set. |

### `project_pages`  (dictionary name: `pages`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| page_id | uuid |  | Y | PK |  |  | Project page ID. |
| project_id | uuid |  | Y | FK |  | projects | Project ID. |
| name | varchar | 255 | Y |  |  |  | Builder page name. |
| path | varchar | 255 | Y |  |  |  | Public URL path. |
| puck_tree | jsonb |  | Y |  |  |  | Editable Puck component tree. |
| seo_title | varchar | 255 |  |  |  |  | Optional search-result title. |
| seo_description | varchar | 500 |  |  |  |  | Optional search-result description. |
| sort_order | integer |  | Y |  |  |  | Navigation display order. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |
| deleted_at | timestamptz |  |  |  |  |  | Deletion time; cannot be changed once set. |

### `project_generation_contracts`  (dictionary name: `generation_contracts`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| generation_contract_id | uuid |  | Y | PK |  |  | ID of the saved generation rules. |
| project_id | uuid |  | Y | FK |  | projects | Project ID. |
| version_number | integer |  | Y |  |  |  | Version number within this project. |
| api_contract_version | varchar | 100 | Y |  |  |  | Version of the approved data and sign-in rules. |
| created_by_user_id | uuid |  | Y |  |  | users | Confirmed user who saved the generation rules. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |

### `project_generation_contract_schemas`  (dictionary name: `generation_contract_schemas`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| generation_contract_id | uuid |  | Y | PK/FK |  | generation_contracts | Saved rules for this generation. |
| product_type_schema_id | uuid |  | Y | PK |  | product_type_schemas | Selected version of the product field rules. |
| product_type_id | uuid |  | Y |  |  | product_types | Product type from the shared catalog. |

### `project_generation_requests`  (dictionary name: `generation_requests`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| generation_request_id | uuid |  | Y | PK |  |  | Generation and Payment attempt ID. |
| project_id | uuid |  | Y | FK |  | projects | Project ID. |
| idempotency_key | varchar | 255 | Y |  |  |  | Key that prevents duplicate requests on retry. |
| request_hash | char | 64 | Y |  |  |  | SHA-256 hash of the request in a fixed format. |
| generation_contract_id | uuid |  | Y | FK |  | generation_contracts | Saved rules for this generation. |
| captured_edit_revision | bigint |  | Y |  |  |  | Editor version when the input was saved. |
| captured_input | jsonb |  | Y |  |  |  | Saved input used for generation. |
| captured_input_hash | char | 64 | Y |  |  |  | SHA-256 hash of the input in a fixed format. |
| accepted_generation_run_id | uuid |  |  |  |  | harness_runs | Accepted AI conversion run ID. |
| source_commit_sha | varchar | 64 |  |  |  |  | Published Git commit; cannot change once set. |
| status | enum |  | Y |  |  |  | Values: CAPTURED, GENERATING, PUBLISHING, PUBLISHED, FAILED. |
| failure_code | varchar | 100 |  |  |  |  | Failure code with sensitive details removed. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |

## catalog-service (Business Portal data)

### `catalog_business_types`  (dictionary name: `business_types`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| business_type_id | uuid |  | Y | PK |  |  | Business type ID. |
| code | varchar | 50 | Y |  |  |  | Unique stable code. |
| name | varchar | 100 | Y |  |  |  | Display name. |
| is_active | boolean |  | Y |  |  |  | Selectable for new websites. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |

### `catalog_websites`  (dictionary name: `websites`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| website_id | uuid |  | Y | PK |  | projects | Uses the same ID as its project. |
| business_type_id | uuid |  | Y | FK |  | business_types | Selected business type; cannot change. |
| db_role_name | varchar | 63 | Y |  |  |  | Unique website database login. |
| status | enum |  | Y |  |  |  | Values: ACTIVE, SUSPENDED, ARCHIVED. |
| storage_limit_bytes | bigint |  | Y |  |  |  | Website media storage limit. |
| max_image_bytes | bigint |  | Y |  |  |  | Maximum image or logo size. |
| storage_used_bytes | bigint |  | Y |  |  |  | Media bytes used; updated by the database. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |

### `catalog_business_profiles`  (dictionary name: `business_profiles`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| website_id | uuid |  | Y | PK/FK |  | websites | Website owning this profile. |
| business_name | varchar | 255 | Y |  |  |  | Public business name. |
| description | text |  |  |  |  |  | Public business description. |
| logo_storage_key | varchar | 500 |  |  |  |  | Logo storage key for this website only. |
| logo_byte_size | bigint |  |  |  |  |  | Logo size counted toward storage. |
| phone | varchar | 50 |  |  |  |  | Public contact number. |
| email | varchar | 320 |  |  |  |  | Contact email address. |
| address | text |  |  |  |  |  | Public business address. |
| timezone | varchar | 100 | Y |  |  |  | Named time zone used for opening hours. |
| opening_hours | jsonb |  | Y |  | [] |  | Local opening days and times. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |

### Catalog tables only on the ERD (not yet in the Data Dictionary — confirm before relying on them)

#### `catalog_product_types`
| Field | Type | Note |
|---|---|---|
| product_type_id | uuid | PK |
| business_type_id | uuid | FK → catalog_business_types |
| code | varchar(50) |  |
| name | varchar(100) |  |
| is_active | boolean |  |
| created_at | timestamptz |  |

#### `catalog_product_type_schemas`
| Field | Type | Note |
|---|---|---|
| product_type_schema_id | uuid | PK |
| product_type_id | uuid | FK → catalog_product_types |
| version_number | integer |  |
| field_schema | jsonb | custom product fields (JSON Schema) |
| is_active | boolean |  |
| created_at | timestamptz |  |

#### `catalog_categories`
| Field | Type | Note |
|---|---|---|
| category_id | uuid | PK |
| website_id | uuid | FK → catalog_websites |
| name | varchar(100) |  |
| slug | varchar(100) |  |
| sort_order | integer |  |
| created_at | timestamptz |  |
| updated_at | timestamptz |  |

#### `catalog_products`
| Field | Type | Note |
|---|---|---|
| product_id | uuid | PK |
| website_id | uuid | FK → catalog_websites |
| category_id | uuid | FK → catalog_categories |
| product_type_schema_id | uuid | FK → catalog_product_type_schemas |
| name | varchar(255) |  |
| description | text |  |
| price | numeric(12,2) |  |
| attributes | jsonb | values for the custom fields |
| status | enum |  |
| sort_order | integer |  |
| row_version | bigint | optimistic lock |
| created_at | timestamptz |  |
| updated_at | timestamptz |  |

#### `catalog_product_images`
| Field | Type | Note |
|---|---|---|
| product_image_id | uuid | PK |
| website_id | uuid | FK → catalog_websites |
| product_id | uuid | FK → catalog_products |
| storage_key | varchar(500) |  |
| byte_size | bigint |  |
| alt_text | varchar(500) |  |
| sort_order | integer |  |
| created_at | timestamptz |  |

## ai-service (codegen + editor assistant)

### `ai_assistant_conversations`  (dictionary name: `conversations`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| conversation_id | uuid |  | Y | PK |  |  | Chat thread ID. |
| project_id | uuid |  | Y |  |  | projects | Project being edited. |
| user_id | uuid |  | Y |  |  | users | User who chats. |
| title | varchar | 255 |  |  |  |  | Short name shown in the chat list. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| last_message_at | timestamptz |  | Y |  |  |  | Time of the newest message. |

### `ai_assistant_messages`  (dictionary name: `messages`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| message_id | uuid |  | Y | PK |  |  | Chat message ID. |
| conversation_id | uuid |  | Y | FK |  | conversations | Thread of this message. |
| message_number | integer |  | Y |  |  |  | Order in the thread; unique per thread. |
| role | enum |  | Y |  |  |  | Values: USER, ASSISTANT. |
| content | text |  | Y |  |  |  | Message text. |
| page_id | uuid |  |  |  |  | pages | Page open in the editor. |
| model_name | varchar | 255 |  |  |  |  | AI model that wrote the reply. |
| input_tokens | integer |  |  |  |  |  | Tokens the model read for the reply. |
| output_tokens | integer |  |  |  |  |  | Tokens the model wrote for the reply. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |

### `ai_assistant_change_sets`  (dictionary name: `change_sets`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| change_set_id | uuid |  | Y | PK |  |  | Change set ID. |
| message_id | uuid |  | Y | FK |  | messages | AI reply that proposes the edits; unique. |
| conversation_id | uuid |  | Y | FK |  | conversations | Thread of this change set. |
| project_id | uuid |  | Y |  |  | projects | Project ID. |
| page_id | uuid |  | Y |  |  | pages | Page the edits apply to. |
| component_catalog_version | varchar | 100 | Y |  |  |  | Component catalog version checked. |
| base_edit_revision | bigint |  | Y |  |  |  | Project save number the edits are based on. |
| status | enum |  | Y |  |  |  | Values: PROPOSED, APPLIED, DISCARDED, REJECTED. |
| rejection_code | varchar | 100 |  |  |  |  | Why the policy check failed. |
| applied_edit_revision | bigint |  |  |  |  |  | Project save number after the client saved. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| decided_at | timestamptz |  |  |  |  |  | Time the final status was set. |

### `ai_assistant_change_items`  (dictionary name: `change_items`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| change_item_id | uuid |  | Y | PK |  |  | Change item ID. |
| change_set_id | uuid |  | Y | FK |  | change_sets | Change set of this item. |
| item_order | integer |  | Y |  |  |  | Order of applying; unique per set. |
| operation | enum |  | Y |  |  |  | Values: ADD_COMPONENT, UPDATE_PROPS, DELETE_COMPONENT, MOVE_COMPONENT. |
| component_id | varchar | 255 | Y |  |  |  | Puck component instance ID. |
| component_type | varchar | 100 | Y |  |  |  | Component type, for example Hero. |
| position | jsonb |  |  |  |  |  | Parent zone and index; add and move only. |
| props_patch | jsonb |  |  |  |  |  | Props to set; add and update only. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |

### `ai_codegen_puck_component_definitions`  (dictionary name: `puck_component_definitions`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| puck_component_definition_id | uuid |  | Y | PK |  |  | Component definition ID. |
| component_catalog_version | varchar | 100 | Y |  |  |  | Component catalog version. |
| component_type | varchar | 100 | Y |  |  |  | Component type; unique per catalog version. |
| component_label | varchar | 255 | Y |  |  |  | Human-readable component name. |
| editable_props_schema | jsonb |  | Y |  |  |  | JSON Schema of the props the AI may edit. |
| allowed_operations | jsonb |  | Y |  |  |  | Operations the AI may use on this component. |
| implementation_mode | enum |  | Y |  |  |  | Values: PUCK_JSON, SOURCE_CODE, MIXED. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time; rows never change. |

### `ai_codegen_input_versions`  (dictionary name: `input_versions`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| input_version_id | uuid |  | Y | PK |  |  | Frozen input ID. |
| project_id | uuid |  | Y |  |  | projects | Project ID. |
| generation_request_id | uuid |  | Y |  |  | generation_requests | Generation request this copies; unique. |
| generation_contract_id | uuid |  | Y |  |  | generation_contracts | Saved rules for this generation. |
| captured_edit_revision | bigint |  | Y |  |  |  | Project save number at capture. |
| component_catalog_version | varchar | 100 | Y |  |  |  | Component catalog version in the input. |
| captured_input | jsonb |  | Y |  |  |  | Exact input captured by Project. |
| input_hash | char | 64 | Y |  |  |  | SHA-256 hash of the input in a fixed format. |
| baseline_commit_sha | varchar | 64 |  |  |  |  | Previous published Git commit; empty on first generation. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |

### `ai_codegen_harness_runs`  (dictionary name: `harness_runs`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| harness_run_id | uuid |  | Y | PK |  |  | AI conversion attempt ID. |
| project_id | uuid |  | Y |  |  | projects | Project ID. |
| generation_request_id | uuid |  | Y |  |  | generation_requests | Generation request of this attempt. |
| generation_contract_id | uuid |  | Y |  |  | generation_contracts | Saved rules for this generation. |
| baseline_version_id | uuid |  | Y | FK |  | input_versions | Frozen input this attempt converts. |
| input_hash | char | 64 | Y |  |  |  | Hash of the captured input. |
| strategy | enum |  | Y |  |  |  | Values: PUCK_JSON, SOURCE_CODE, MIXED. |
| status | enum |  | Y |  |  |  | Values: QUEUED, RUNNING, SUCCEEDED, FAILED. |
| attempt | integer |  | Y |  |  |  | Attempt number; unique per request. |
| branch_name | varchar | 255 |  |  |  |  | Optional Gitea work branch. |
| model_name | varchar | 255 |  |  |  |  | AI model used. |
| input_tokens | integer |  |  |  |  |  | Tokens the model read in this attempt. |
| output_tokens | integer |  |  |  |  |  | Tokens the model wrote in this attempt. |
| errors | text |  |  |  |  |  | Cleaned error details when failed. |
| error_code | varchar | 100 |  |  |  |  | Error code when failed. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| started_at | timestamptz |  |  |  |  |  | Worker start time. |
| completed_at | timestamptz |  |  |  |  |  | Finish time. |

### `ai_codegen_validation_runs`  (dictionary name: `validation_runs`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| validation_run_id | uuid |  | Y | PK |  |  | Check try ID. |
| harness_run_id | uuid |  | Y | FK |  | harness_runs | Attempt being checked. |
| validation_type | enum |  | Y |  |  |  | Values: PUCK_SCHEMA, TYPE_CHECK, BUILD, LINT, SMOKE_TEST, SECURITY. |
| attempt | integer |  | Y |  |  |  | Try number; unique per run and check type. |
| required | boolean |  | Y |  |  |  | Must pass for the attempt to succeed. |
| status | enum |  | Y |  |  |  | Values: PENDING, RUNNING, SUCCESS, FAILED. |
| result | jsonb |  |  |  |  |  | Cleaned check details. |
| error | text |  |  |  |  |  | Error details when failed. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| started_at | timestamptz |  |  |  |  |  | Check start time. |
| completed_at | timestamptz |  |  |  |  |  | Check finish time. |

### `ai_codegen_artifacts`  (dictionary name: `artifacts`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| artifact_id | uuid |  | Y | PK |  |  | Output file ID. |
| harness_run_id | uuid |  | Y | FK |  | harness_runs | Attempt that produced the file. |
| artifact_type | enum |  | Y |  |  |  | Values: SOURCE_ARCHIVE, CHECK_REPORT. |
| storage_ref | varchar | 500 | Y |  |  |  | Private storage location of the file. |
| content_hash | char | 64 | Y |  |  |  | SHA-256 hash of the file bytes. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |

## provisioning-service (deploy, subdomain, hosting)

### `provisioning_releases`  (dictionary name: `releases`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| release_id | uuid |  | Y | PK |  |  | Provisioning release ID. |
| project_id | uuid |  | Y |  |  | projects | Project ID. |
| generation_request_id | uuid |  | Y |  |  | generation_requests | Generation request that published this Git commit. |
| repository_id | bigint |  | Y |  |  |  | Gitea repository ID. |
| git_commit_sha | varchar | 64 | Y |  |  |  | Accepted Git commit; cannot change. |
| container_image_digest | varchar | 71 |  |  |  |  | Hash identifying the built container image. |
| status | enum |  | Y |  |  |  | Values: ACCEPTED, BUILDING, BUILT, CANDIDATE_READY, PROMOTING, LIVE, SUPERSEDED, FAILED, ROLLED_BACK. |
| failure_code | varchar | 100 |  |  |  |  | Failure code with sensitive details removed. |
| failure_summary | text |  |  |  |  |  | Safe failure explanation. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |
| promoted_at | timestamptz |  |  |  |  |  | The release was confirmed live. |

### `provisioning_project_hosting`  (dictionary name: `project_hosting`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| project_id | uuid |  | Y | PK/FK |  | releases | Hosted project ID. |
| live_release_id | uuid |  |  | FK |  | releases | Last release confirmed to be serving the site. |
| subdomain | varchar | 63 | Y |  |  |  | Copy of the project's sub-domain used for routing. |
| last_command_number | bigint |  | Y |  |  |  | Number increases for each accepted command. |
| routing_status | enum |  | Y |  |  |  | Values: STABLE, CHANGING, UNCERTAIN. |
| hosting_status | enum |  | Y |  |  |  | Values: ONLINE, OFFLINE. |
| offline_reasons | enum[] |  | Y |  |  |  | One or more of: BILLING, USER_SUSPENDED, PROJECT_DELETED, ADMIN_SUSPENDED. Empty when ONLINE. |
| owner_status_version | bigint |  | Y |  |  |  | Latest owner status change number received. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |

### `provisioning_release_steps`  (dictionary name: `release_steps`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| release_step_id | uuid |  | Y | PK |  |  | Release step attempt ID. |
| release_id | uuid |  | Y | FK |  | releases | Provisioning release ID. |
| step_name | enum |  | Y |  |  |  | Values: BUILD, ACCESS_SETUP, CANDIDATE, PROMOTION, OBSERVATION, ROLLBACK. ACCESS_SETUP and OBSERVATION are unused. |
| attempt | integer |  | Y |  |  |  | Retry number, starting at 1. |
| status | enum |  | Y |  |  |  | Values: PENDING, RUNNING, SUCCEEDED, FAILED. |
| started_at | timestamptz |  |  |  |  |  | Execution start time. |
| finished_at | timestamptz |  |  |  |  |  | Execution finish time. |
| deadline_at | timestamptz |  |  |  |  |  | Deadline for the step that is running. |
| failure_code | varchar | 100 |  |  |  |  | Failure code with sensitive details removed. |
| log_ref | varchar | 500 |  |  |  |  | Private ID used to find the log. |

### `provisioning_commands`  (dictionary name: `commands`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| command_id | uuid |  | Y | PK |  |  | Command ID. |
| project_id | uuid |  | Y | FK |  | releases | Project ID. |
| release_id | uuid |  |  | FK |  | releases | Provisioning release ID. |
| kind | enum |  | Y |  |  |  | Values: DEPLOY, PROMOTE, ROLLBACK, SUSPEND_HOSTING, RESUME_HOSTING, SYNC_DOMAINS, REDEPLOY. SYNC_DOMAINS is unused. |
| hosting_reason | enum |  |  |  |  |  | Values: BILLING, USER_SUSPENDED, PROJECT_DELETED, ADMIN_SUSPENDED. |
| idempotency_key | varchar | 255 | Y |  |  |  | Key that prevents duplicate requests on retry. |
| request_fingerprint | char | 64 | Y |  |  |  | SHA-256 hash of the command in a fixed format. |
| command_number | bigint |  | Y |  |  |  | Number used to block outdated commands. |
| requested_by_subject | varchar | 255 |  |  |  | Keycloak sub | Keycloak ID of the user requesting the command. |
| status | enum |  | Y |  |  |  | Values: ACCEPTED, RUNNING, SUCCEEDED, FAILED, SKIPPED. |
| failure_code | varchar | 100 |  |  |  |  | Failure code with sensitive details removed. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |

## payment-service (plans, subscriptions, WeBill 365 KHQR)

### `payment_client_trials`  (dictionary name: `client_trials`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| client_user_id | uuid |  | Y | PK |  | users | Client with one lifetime trial. |
| project_id | uuid |  |  |  |  | projects | Selected trial website. |
| starts_at | timestamptz |  | Y |  |  |  | The time the client first signed up. |
| ends_at | timestamptz |  | Y |  |  |  | Scheduled trial ends. |
| generation_limit | smallint |  | Y |  | 3 |  | Maximum trial generations. |
| product_limit | smallint |  | Y |  | 30 |  | Maximum products in the trial website. |
| ended_at | timestamptz |  |  |  |  |  | Early trial termination time. |

### `payment_plans`  (dictionary name: `plans`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| plan_id | uuid |  | Y | PK |  |  | Payment plan ID. |
| code | varchar | 50 | Y |  |  |  | Plus, Pro or Ultra code. |
| name | varchar | 100 | Y |  |  |  | Display name. |
| billing_interval | enum |  | Y |  |  |  | Values: MONTH. |
| limits | jsonb |  | Y |  |  |  | Website, redeploy, product, monitoring and logs limits. |
| active | boolean |  | Y |  |  |  | Available for new subscriptions. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |
| updated_by | varchar | 255 |  |  |  | Keycloak sub | Keycloak ID of the admin who last changed it. |

### `payment_plan_prices`  (dictionary name: `plan_prices`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| plan_price_id | uuid |  | Y | PK |  |  | Dated price ID. |
| plan_id | uuid |  | Y | FK |  | plans | Payment plan ID. |
| price_usd | numeric | 10,2 | Y |  |  |  | Price per billing interval. |
| effective_from | timestamptz |  | Y |  |  |  | Time this price takes effect. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| created_by | varchar | 255 | Y |  |  | Keycloak sub | Keycloak ID of the admin creating the record. |

### `payment_plan_changes`  (dictionary name: `plan_changes`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| plan_change_id | uuid |  | Y | PK |  |  | ID of the record tracking a plan change. |
| plan_id | uuid |  | Y | FK |  | plans | Payment plan ID. |
| subscription_id | uuid |  |  | FK |  | subscriptions | Admin-upgraded subscription. |
| changed_by | varchar | 255 | Y |  |  | Keycloak sub | Keycloak ID of the admin making the change. |
| changed_at | timestamptz |  | Y |  |  |  | Change time. |
| field | varchar | 50 | Y |  |  |  | Changed plan field. |
| old_value | jsonb |  |  |  |  |  | Value before the change. |
| new_value | jsonb |  |  |  |  |  | Value after the change. |

### `payment_subscriptions`  (dictionary name: `subscriptions`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| subscription_id | uuid |  | Y | PK |  |  | Related subscription. |
| client_user_id | uuid |  | Y |  |  | users | Paying client ID. |
| project_id | uuid |  | Y |  |  | projects | Covered project; redesign pending. |
| plan_id | uuid |  | Y | FK |  | plans | Payment plan ID. |
| price_usd | numeric | 10,2 | Y |  |  |  | Price held until renewal. |
| status | enum |  | Y |  |  |  | Values: PENDING, ACTIVE, PAST_DUE, CANCELED, EXPIRED. |
| started_at | timestamptz |  | Y |  |  |  | Original subscription starts. |
| current_period_start | timestamptz |  | Y |  |  |  | Current access period starts. |
| current_period_end | timestamptz |  | Y |  |  |  | Current access period ends. |
| grace_until | timestamptz |  |  |  |  |  | Access deadline when payment is overdue. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |

### `payment_usage_records`  (dictionary name: `usage_records`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| usage_record_id | uuid |  | Y | PK |  |  | Usage entry ID. |
| client_user_id | uuid |  | Y |  |  | users | Paying client ID. |
| project_id | uuid |  | Y |  |  | projects | Project ID. |
| release_id | uuid |  |  |  |  | releases | Release that completed publication. |
| usage_type | enum |  | Y |  |  |  | Values: PROJECT_CREATED, PUBLISH_COMPLETED, GENERATION_ATTEMPT. |
| quantity | integer |  | Y |  |  |  | Number of units used; greater than zero. |
| event_key | varchar | 255 | Y |  |  |  | Unique key identifying the original event. |
| recorded_at | timestamptz |  | Y |  |  |  | Usage recording time. |

### `payment_client_status`  (dictionary name: `client_status`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| client_user_id | uuid |  | Y | PK |  | users | Paying client ID. |
| status | enum |  | Y |  |  |  | Values: ACTIVE, SUSPENDED. |
| status_version | bigint |  | Y |  |  |  | Latest account status change number received. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |

### `payment_bills`  (dictionary name: `bills`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| bill_id | uuid |  | Y | PK |  |  | Platform bill ID. |
| client_user_id | uuid |  | Y |  |  | users | Paying client ID. |
| project_id | uuid |  | Y |  |  | projects | Billed project; redesign pending. |
| subscription_id | uuid |  | Y | FK |  | subscriptions | Related subscription. |
| plan_price_id | uuid |  | Y | FK |  | plan_prices | Plan price used for this bill. |
| period_start | timestamptz |  | Y |  |  |  | Billed period starts. |
| period_end | timestamptz |  | Y |  |  |  | Billed period ends. |
| amount | numeric | 18,2 | Y |  |  |  | Amount due; greater than zero. |
| account_name | varchar | 120 | Y |  |  |  | Name displayed on KHQR. |
| payment_type | varchar | 2 | Y |  |  |  | Webill payment type code. |
| currency_code | varchar | 3 | Y |  |  |  | Three-letter currency code. |
| issue_datetime | timestamptz |  | Y |  |  |  | Bill issue time. |
| payment_term | smallint |  | Y |  |  |  | Time allowed to pay; time unit not confirmed. |
| parent_account_no | varchar | 20 | Y |  |  |  | Merchant parent account. |
| remark | varchar | 255 |  |  |  |  | Optional billing note. |
| bill_no | varchar | 32 |  |  |  |  | Unique Webill bill number. |
| account_no | varchar | 20 |  |  |  |  | QR receiving account. |
| khqr_data | text |  |  |  |  |  | Text used to generate the KHQR payment code. |
| status_bill_no | varchar | 32 |  |  |  |  | Bill number from status response. |
| paid_amount | numeric | 18,2 | Y |  |  |  | Amount actually paid. |
| status_code | varchar | 2 |  |  |  |  | Original Webill status code. |
| status_name | varchar | 30 |  |  |  |  | Webill status text. |
| status | enum |  | Y |  |  |  | Values: PENDING, PAID, EXPIRED, FAILED, VOID. |
| failure_code | varchar | 100 |  |  |  |  | Failure code with sensitive details removed. |
| expires_at | timestamptz |  | Y |  |  |  | Bill payment deadline. |
| last_status_check_at | timestamptz |  |  |  |  |  | Last time Webill payment status was checked. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |
| updated_at | timestamptz |  | Y |  |  |  | Last update time. |
| paid_at | timestamptz |  |  |  |  |  | Confirmed payment time. |

### `payment_transactions`  (dictionary name: `transactions`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| payment_transaction_id | uuid |  | Y | PK |  |  | Platform transaction ID. |
| bill_id | uuid |  | Y | FK |  | bills | Related bill ID. |
| transaction_id | bigint |  | Y |  |  |  | Unique Webill transaction ID. |
| transaction_date | date |  | Y |  |  |  | Payer payment date. |
| transaction_time | time |  | Y |  |  |  | Payer payment local time. |
| transacted_at | timestamptz |  | Y |  |  |  | Date and time paid in Phnom Penh time. |
| account_name | varchar | 120 | Y |  |  |  | Receiving account name. |
| currency_code | varchar | 3 | Y |  |  |  | Three-letter currency code. |
| amount | numeric | 18,2 | Y |  |  |  | Transaction amount. |
| sender_bank_name | varchar | 60 |  |  |  |  | Payer bank name. |
| sender_account_no | varchar | 30 |  |  |  |  | Payer account number. |
| sender_account_name | text |  |  |  |  |  | Payer account name. |
| sender_remark | text |  |  |  |  |  | Payer payment note. |
| payment_method | varchar | 10 |  |  |  |  | Webill payment method code. |
| settlement_date | date |  |  |  |  |  | Settlement date. |
| settlement_time | time |  |  |  |  |  | Settlement time. |
| settlement_status | varchar | 2 |  |  |  |  | Settlement status code. |
| settlement_error_message | text |  |  |  |  |  | Settlement failure details. |
| created_at | timestamptz |  | Y |  |  |  | Record creation time. |

### `payment_payment_events`  (dictionary name: `payment_events`)

| Field | Type | Len | Req | Key | Default | Ref | Description |
|---|---|---|---|---|---|---|---|
| payment_event_id | uuid |  | Y | PK |  |  | Payment event ID. |
| bill_id | uuid |  | Y | FK |  | bills | Related bill ID. |
| event_key | varchar | 255 | Y |  |  |  | Unique key that prevents duplicate processing. |
| provider_event_id | varchar | 255 |  |  |  |  | Webill event ID, if supplied. |
| payload_ref | varchar | 500 |  |  |  |  | Private location of Webill response data. |
| received_at | timestamptz |  | Y |  |  |  | Time the payment result was received or checked. |
| processed_at | timestamptz |  |  |  |  |  | Successful handling time. |
