#!/usr/bin/env bash
# ==============================================================================
# WebBlock Platform: Quick ID Generator (gen-ids.sh)
#
# Generates dynamic IDs needed for testing WebBlock APIs:
#   - PROJECT_ID      : Canonical lowercase UUID v4
#   - IDEMPOTENCY_KEY : Unique idempotency key for generation requests
#   - PRODUCT_TYPE_ID : Pre-mapped catalog product type UUID by business category
#
# Usage:
#   ./gen-ids.sh                  # Prints ready-to-use export block for testing
#   ./gen-ids.sh project          # Prints only a Project ID
#   ./gen-ids.sh idemp            # Prints only an Idempotency Key
#   ./gen-ids.sh product [type]   # Prints Catalog ProductType ID (food, tech, beauty, furniture, fashion)
#   eval $(./gen-ids.sh)          # Automatically exports all variables into current shell
# ==============================================================================

# Helper to generate canonical lowercase UUID
gen_uuid() {
    if command -v uuidgen >/dev/null 2>&1; then
        uuidgen | tr '[:upper:]' '[:lower:]'
    elif [ -f /proc/sys/kernel/random/uuid ]; then
        cat /proc/sys/kernel/random/uuid
    elif command -v python3 >/dev/null 2>&1; then
        python3 -c "import uuid; print(uuid.uuid4())"
    else
        od -x /dev/urandom | head -1 | awk '{OFS="-"; print $2$3,$4,$5,$6,$7$8$9}'
    fi
}

get_product_type_id() {
    local category="${1:-food}"
    case "$category" in
        food|food_shop|bakery|restaurant|cafe)
            echo "1617e841-f602-4ccd-b9ec-0f9dfc9afbf8" # menu_item
            ;;
        tech|tech_shop|device|electronics)
            echo "4b7b261e-a4cb-44d2-b7ee-609b458911cd" # tech_device
            ;;
        beauty|beauty_shop|salon|cosmetics)
            echo "01eb1184-c258-423a-9ba2-d8c4be996c27" # beauty_product
            ;;
        furniture|furniture_shop|decor)
            echo "b825a05f-b200-4d29-baab-a4fbf36b4902" # furniture_item
            ;;
        fashion|clothing|clothing_shop)
            echo "a9693e48-969a-4f26-984c-d30d931bae1f" # top
            ;;
        *)
            echo "1617e841-f602-4ccd-b9ec-0f9dfc9afbf8" # default: food_shop menu_item
            ;;
    esac
}

MODE="${1:-all}"

case "$MODE" in
    project|proj|-p)
        gen_uuid
        ;;
    idemp|idempotency|-i)
        echo "idemp-$(gen_uuid)"
        ;;
    product|prod)
        get_product_type_id "${2:-food}"
        ;;
    all|--export)
        PROJECT_ID="$(gen_uuid)"
        IDEMPOTENCY_KEY="idemp-$(gen_uuid)"
        PRODUCT_TYPE_ID="$(get_product_type_id "${2:-food}")"

        echo "export PROJECT_ID=\"$PROJECT_ID\""
        echo "export IDEMPOTENCY_KEY=\"$IDEMPOTENCY_KEY\""
        echo "export PRODUCT_TYPE_ID=\"$PRODUCT_TYPE_ID\""
        ;;
    help|-h|--help)
        echo "Usage: ./gen-ids.sh [command] [options]"
        echo ""
        echo "Commands:"
        echo "  (no args)           Print all variables formatted as 'export VAR=value'"
        echo "  project             Generate a single Project UUID"
        echo "  idemp               Generate a single Idempotency Key"
        echo "  product [category]  Get Catalog ProductType ID for category:"
        echo "                      (food, tech, beauty, furniture, fashion)"
        echo ""
        echo "Examples:"
        echo "  eval \$(./gen-ids.sh)            # Load into current shell directly"
        echo "  ./gen-ids.sh product tech       # 4b7b261e-a4cb-44d2-b7ee-609b458911cd"
        ;;
    *)
        echo "Unknown option: $MODE. Run './gen-ids.sh --help' for options." >&2
        exit 1
        ;;
esac
