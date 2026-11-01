-- account_users and account_user_status_changes
-- Source: docs/specs/data-dictionary.md (account-service) and docs/specs/status-flows.md
-- Columns match the Data Dictionary exactly. Profile photo lives in Keycloak (user attribute "picture").
-- Enums are stored as text (Java: @Enumerated(EnumType.STRING)), limited by CHECK constraints.

CREATE TABLE account_users (
    user_id          uuid         PRIMARY KEY,
    identity_issuer  varchar(255) NOT NULL,            -- Keycloak realm URL (token "iss")
    identity_subject varchar(255) NOT NULL,            -- Keycloak user id (token "sub")
    email            varchar(320) NOT NULL,
    display_name     varchar(255),                     -- full name shown in Settings › Profile
    status           varchar(20)  NOT NULL DEFAULT 'ACTIVE',
    status_version   bigint       NOT NULL DEFAULT 0,  -- goes up by 1 on every status change
    created_at       timestamptz  NOT NULL DEFAULT now(),
    updated_at       timestamptz  NOT NULL DEFAULT now(),
    last_login_at    timestamptz,

    CONSTRAINT account_users_status_chk
        CHECK (status IN ('ACTIVE', 'SUSPENDED')),
    CONSTRAINT account_users_status_version_chk
        CHECK (status_version >= 0),
    -- one Keycloak identity = one account
    CONSTRAINT account_users_identity_uq
        UNIQUE (identity_issuer, identity_subject)
);

-- one email = one account (case-insensitive: Sara@x.com and sara@x.com are the same)
CREATE UNIQUE INDEX account_users_email_uq ON account_users (lower(email));

CREATE TABLE account_user_status_changes (
    user_status_change_id uuid         PRIMARY KEY,
    user_id               uuid         NOT NULL REFERENCES account_users (user_id),
    status_version        bigint       NOT NULL,
    from_status           varchar(20)  NOT NULL,
    to_status             varchar(20)  NOT NULL,
    reason_code           varchar(20),
    note                  varchar(500),             -- private admin note
    changed_by            varchar(255),             -- admin Keycloak id; NULL for a system change
    changed_at            timestamptz  NOT NULL DEFAULT now(),

    CONSTRAINT account_user_status_changes_from_chk
        CHECK (from_status IN ('ACTIVE', 'SUSPENDED')),
    CONSTRAINT account_user_status_changes_to_chk
        CHECK (to_status IN ('ACTIVE', 'SUSPENDED')),
    CONSTRAINT account_user_status_changes_reason_chk
        CHECK (reason_code IS NULL OR reason_code IN ('ABUSE', 'FRAUD', 'OWNER_REQUEST', 'OTHER')),
    -- each version number happens only once per user
    CONSTRAINT account_user_status_changes_version_uq
        UNIQUE (user_id, status_version)
);

CREATE INDEX account_user_status_changes_user_idx
    ON account_user_status_changes (user_id, changed_at DESC);
