#!/usr/bin/env bash

set -euo pipefail

# Global configuration
readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly CREATED_OBJECTS_FILE="${SCRIPT_DIR}/created_objects.jsonl"
readonly RUN_TAG="$(date +%Y%m%d-%H%M%S)-$$"
readonly PREFIX="ai-test-${RUN_TAG}"

# Test counters
PASSED=0
FAILED=0
SKIPPED=0

# Required tools check
command -v curl >/dev/null 2>&1 || { echo "ERROR: curl is required but not installed." >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required but not installed." >&2; exit 1; }

# Environment validation
require_env() {
    local var_name="$1"
    if [[ -z "${!var_name:-}" ]]; then
        echo "ERROR: Environment variable $var_name is required" >&2
        exit 1
    fi
}

BASE_URL="http://localhost:7999"
GUILD_ID="711548456019296289"
TEST_USER_ID="640882072516427787"

# Set defaults
BASE_URL="${BASE_URL:-http://localhost:7999}"
SKIP_CLEANUP="${SKIP_CLEANUP:-0}"
VERBOSE="${VERBOSE:-1}"


# Initialize tracking and log files
> "$CREATED_OBJECTS_FILE"
> "$LOG_FILE"

# Enhanced logging function
log() {
    local message="$*"
    local timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$timestamp] $message" | tee -a "$LOG_FILE"
    [[ "$VERBOSE" == "1" ]] && echo "[$timestamp] $message" >&2
}

# Log to file only (for detailed request/response logging)
log_detail() {
    local timestamp="$(date '+%Y-%m-%d %H:%M:%S')"
    echo "[$timestamp] $*" >> "$LOG_FILE"
}

# Failure reporting function (non-fatal)
fail() {
    local test_name="$1"
    local reason="$2"
    echo "FAIL: $test_name - $reason" >&2
    FAILED_TESTS+=("$test_name: $reason")
    log "FAIL: $test_name - $reason"
    ((FAILED++))
    return 1
}

# Success reporting function
pass() {
    local test_name="$1"
    log "PASS: $test_name"
    ((PASSED++))
}

# Skip reporting function
skip() {
    local test_name="$1"
    local reason="$2"
    log "SKIP: $test_name - $reason"
    ((SKIPPED++))
}

# Enhanced HTTP client with detailed logging
api() {
    local method="$1"
    local endpoint="$2"
    local data="${3:-}"
    local max_retries=3
    local retry_count=0
    local wait_time=1
    
    local url="${BASE_URL}${endpoint}"
    local curl_args=(
        -X "$method"
        -H "Content-Type: application/json"
        -H "Accept: application/json"
        --max-time 30
        --connect-timeout 10
        -s
        -w "HTTPSTATUS:%{http_code}"
    )
    
    # Add auth header if provided
    if [[ -n "${AUTH_HEADER:-}" ]]; then
        curl_args+=(-H "$AUTH_HEADER")
    fi
    
    # Add data for non-GET requests
    if [[ -n "$data" && "$method" != "GET" ]]; then
        curl_args+=(-d "$data")
    fi
    
    # Log the request details
    log_detail "REQUEST: $method $url"
    [[ -n "$data" ]] && log_detail "REQUEST BODY: $data"
    
    while [[ $retry_count -lt $max_retries ]]; do
        local response
        response=$(curl "${curl_args[@]}" "$url" 2>/dev/null || echo "HTTPSTATUS:000")
        
        local http_code
        http_code=$(echo "$response" | sed -n 's/.*HTTPSTATUS:\([0-9]*\)$/\1/p')
        local body
        body=$(echo "$response" | sed 's/HTTPSTATUS:[0-9]*$//')
        
        # Log the response details
        log_detail "RESPONSE CODE: $http_code"
        log_detail "RESPONSE BODY: $body"
        
        case "$http_code" in
            200|201|204)
                echo "$body"
                return 0
                ;;
            404)
                # 404 is often expected (e.g., verification after delete)
                log_detail "404 response (may be expected)"
                echo "$body"
                return 1
                ;;
            429)
                # Rate limit - parse Retry-After or use exponential backoff
                local retry_after
                retry_after=$(echo "$body" | jq -r '.retry_after // empty' 2>/dev/null || echo "")
                if [[ -n "$retry_after" ]]; then
                    wait_time="$retry_after"
                fi
                log_detail "Rate limited (429), waiting ${wait_time}s before retry"
                sleep "$wait_time"
                wait_time=$((wait_time * 2))
                ((retry_count++))
                ;;
            5*)
                # Server error - retry with backoff
                log_detail "Server error ($http_code), retrying in ${wait_time}s"
                sleep "$wait_time"
                wait_time=$((wait_time * 2))
                ((retry_count++))
                ;;
            *)
                # Other error - don't retry
                log_detail "HTTP error $http_code - $body"
                echo "$body"
                return 1
                ;;
        esac
    done
    
    log_detail "Max retries exceeded for $method $endpoint"
    return 1
}

# Sleep and verify with GET (non-fatal)
sleep_and_get() {
    local endpoint="$1"
    local expected_jq_expr="$2"
    local actual_jq_expr="$3"
    local test_name="$4"
    
    log_detail "Waiting 5s before verification for: $test_name"
    sleep 5
    
    local response
    if ! response=$(api GET "$endpoint"); then
        fail "$test_name" "GET verification failed for $endpoint"
        return 1
    fi
    
    assert_eq "$expected_jq_expr" "$actual_jq_expr" "$test_name" "$response"
}

# Assertion helper (non-fatal)
assert_eq() {
    local expected="$1"
    local actual_expr="$2"
    local test_name="$3"
    local response="${4:-}"
    
    local actual
    if [[ -n "$response" ]]; then
        actual=$(echo "$response" | jq -r "$actual_expr" 2>/dev/null || echo "null")
    else
        actual="$actual_expr"
    fi
    
    if [[ "$expected" == "$actual" ]]; then
        pass "$test_name assertion ($expected)"
        return 0
    else
        fail "$test_name assertion" "expected '$expected', got '$actual'"
        log_detail "Assertion failure response: $response"
        return 1
    fi
}

# Record created object
record_object() {
    local type="$1"
    local id="$2"
    local name="$3"
    local parent="${4:-null}"
    
    echo "{\"type\":\"$type\",\"id\":\"$id\",\"name\":\"$name\",\"parent\":$parent}" >> "$CREATED_OBJECTS_FILE"
    log_detail "Recorded $type: $id ($name)"
}

# Get permission bitfield for names (non-fatal)
get_permission_bits() {
    local permission_names=("$@")
    local names_json
    names_json=$(printf '%s\n' "${permission_names[@]}" | jq -R . | jq -s .)
    
    local response
    if response=$(api POST "/api/v1/permissions/convert/names-to-value" "{\"names\":$names_json}"); then
        echo "$response" | jq -r '.value // 0'
    else
        log_detail "Failed to get permission bits, using 0"
        echo "0"
    fi
}

# Test categories (continue on failures)
test_categories() {
    log "Testing categories..."
    
    # Test 1: Create category with nsfw=false
    local cat1_payload="{\"name\":\"${PREFIX}-cat-1\",\"nsfw\":false,\"position\":1}"
    local cat1_response
    local cat1_id=""
    if cat1_response=$(api POST "/api/v1/guilds/$GUILD_ID/categories" "$cat1_payload"); then
        cat1_id=$(echo "$cat1_response" | jq -r '.category.id')
        if [[ "$cat1_id" != "null" && -n "$cat1_id" ]]; then
            record_object "category" "$cat1_id" "${PREFIX}-cat-1" "null"
            pass "Category 1 creation"
            
            # Verify category properties
            sleep_and_get "/api/v1/guilds/$GUILD_ID/categories/$cat1_id" "false" ".category.nsfw" "Category 1 nsfw=false"
            sleep_and_get "/api/v1/guilds/$GUILD_ID/categories/$cat1_id" "${PREFIX}-cat-1" ".category.name" "Category 1 name"
        else
            fail "Category 1 creation" "Invalid category ID returned"
        fi
    else
        fail "Category 1 creation" "API call failed"
    fi
    
#    # Test 2: Create category with nsfw=true -> Not valid - categories can't have this set
#    local cat2_payload="{\"name\":\"${PREFIX}-cat-2\",\"nsfw\":true,\"position\":2}"
#    local cat2_response
#    local cat2_id=""
#    if cat2_response=$(api POST "/api/v1/guilds/$GUILD_ID/categories" "$cat2_payload"); then
#        cat2_id=$(echo "$cat2_response" | jq -r '.category.id')
#        if [[ "$cat2_id" != "null" && -n "$cat2_id" ]]; then
#            record_object "category" "$cat2_id" "${PREFIX}-cat-2" "null"
#            pass "Category 2 creation"
#            
#            sleep_and_get "/api/v1/guilds/$GUILD_ID/categories/$cat2_id" "true" ".category.nsfw" "Category 2 nsfw=true"
#        else
#            fail "Category 2 creation" "Invalid category ID returned"
#        fi
#    else
#        fail "Category 2 creation" "API call failed"
#    fi
    
    # Test 3: Update category (only if cat1 exists)
    if [[ -n "$cat1_id" ]]; then
        local update_payload="{\"name\":\"${PREFIX}-cat-1-updated\",\"position\":5}"
        if api PUT "/api/v1/guilds/$GUILD_ID/categories/$cat1_id" "$update_payload" >/dev/null; then
            pass "Category 1 update"
            sleep_and_get "/api/v1/guilds/$GUILD_ID/categories/$cat1_id" "${PREFIX}-cat-1-updated" ".category.name" "Category update name"
            # Not valid for categories, so removed from test case...
            # sleep_and_get "/api/v1/guilds/$GUILD_ID/categories/$cat1_id" "true" ".category.nsfw" "Category update nsfw"
        else
            fail "Category 1 update" "API call failed"
        fi
    else
        skip "Category 1 update" "Category 1 was not created successfully"
    fi
    
    # Test 4: List categories
    if api GET "/api/v1/guilds/$GUILD_ID/categories" >/dev/null; then
        pass "List categories"
    else
        fail "List categories" "API call failed"
    fi
}

# Test channels (continue on failures)
test_channels() {
    log "Testing channels..."
    
    # Get a category ID for testing
    local cat_response
    local test_cat_id=""
    if cat_response=$(api GET "/api/v1/guilds/$GUILD_ID/categories"); then
        test_cat_id=$(echo "$cat_response" | jq -r ".categories[] | select(.name | startswith(\"${PREFIX}\")) | .id" | head -1)
    fi
    
    # Test 1: Create text channel without category
    local chan1_payload="{\"name\":\"${PREFIX}-text-1\",\"type\":\"text\",\"nsfw\":false,\"slowmode_delay\":0}"
    local chan1_response
    local chan1_id=""
    if chan1_response=$(api POST "/api/v1/guilds/$GUILD_ID/channels" "$chan1_payload"); then
        chan1_id=$(echo "$chan1_response" | jq -r '.channel.id')
        if [[ "$chan1_id" != "null" && -n "$chan1_id" ]]; then
            record_object "channel" "$chan1_id" "${PREFIX}-text-1" "null"
            pass "Text channel 1 creation"
            
            sleep_and_get "/api/v1/channels/$chan1_id" "text" ".channel.type" "Text channel type"
            sleep_and_get "/api/v1/channels/$chan1_id" "false" ".channel.nsfw" "Text channel nsfw"
        else
            fail "Text channel 1 creation" "Invalid channel ID returned"
        fi
    else
        fail "Text channel 1 creation" "API call failed"
    fi
    
    # Test 2: Create text channel with category (only if category exists)
    if [[ -n "$test_cat_id" && "$test_cat_id" != "null" ]]; then
        local chan2_payload="{\"name\":\"${PREFIX}-text-2\",\"type\":\"text\",\"category_id\":$test_cat_id,\"nsfw\":false,\"slowmode_delay\":0}"
        local chan2_response
        local chan2_id=""
        if chan2_response=$(api POST "/api/v1/guilds/$GUILD_ID/channels" "$chan2_payload"); then
            chan2_id=$(echo "$chan2_response" | jq -r '.channel.id')
            if [[ "$chan2_id" != "null" && -n "$chan2_id" ]]; then
                record_object "channel" "$chan2_id" "${PREFIX}-text-2" "$test_cat_id"
                pass "Text channel 2 creation"
                
                sleep_and_get "/api/v1/channels/$chan2_id" "$test_cat_id" ".channel.category_id" "Text channel category"
            else
                fail "Text channel 2 creation" "Invalid channel ID returned"
            fi
        else
            fail "Text channel 2 creation" "API call failed"
        fi
    else
        skip "Text channel 2 creation" "No test category available"
    fi
    
    # Test 3: Create NSFW text channel with slowmode
    if [[ -n "$test_cat_id" && "$test_cat_id" != "null" ]]; then
        local chan3_payload="{\"name\":\"${PREFIX}-text-3\",\"type\":\"text\",\"category_id\":$test_cat_id,\"nsfw\":true,\"slowmode_delay\":5}"
        local chan3_response
        local chan3_id=""
        if chan3_response=$(api POST "/api/v1/guilds/$GUILD_ID/channels" "$chan3_payload"); then
            chan3_id=$(echo "$chan3_response" | jq -r '.channel.id')
            if [[ "$chan3_id" != "null" && -n "$chan3_id" ]]; then
                record_object "channel" "$chan3_id" "${PREFIX}-text-3" "$test_cat_id"
                pass "NSFW text channel creation"
                
                sleep_and_get "/api/v1/channels/$chan3_id" "true" ".channel.nsfw" "NSFW channel nsfw"
                sleep_and_get "/api/v1/channels/$chan3_id" "5" ".channel.slowmode_delay" "Channel slowmode"
            else
                fail "NSFW text channel creation" "Invalid channel ID returned"
            fi
        else
            fail "NSFW text channel creation" "API call failed"
        fi
    else
        skip "NSFW text channel creation" "No test category available"
    fi
    
    # Test 4: Create voice channel
    if [[ -n "$test_cat_id" && "$test_cat_id" != "null" ]]; then
        local chan4_payload="{\"name\":\"${PREFIX}-voice-1\",\"type\":\"voice\",\"category_id\":$test_cat_id,\"bitrate\":64000,\"user_limit\":10}"
        local chan4_response
        local chan4_id=""
        if chan4_response=$(api POST "/api/v1/guilds/$GUILD_ID/channels" "$chan4_payload"); then
            chan4_id=$(echo "$chan4_response" | jq -r '.channel.id')
            if [[ "$chan4_id" != "null" && -n "$chan4_id" ]]; then
                record_object "channel" "$chan4_id" "${PREFIX}-voice-1" "$test_cat_id"
                pass "Voice channel creation"
                
                sleep_and_get "/api/v1/channels/$chan4_id" "voice" ".channel.type" "Voice channel type"
                sleep_and_get "/api/v1/channels/$chan4_id" "64000" ".channel.bitrate" "Voice channel bitrate"
                sleep_and_get "/api/v1/channels/$chan4_id" "10" ".channel.user_limit" "Voice channel user limit"
            else
                fail "Voice channel creation" "Invalid channel ID returned"
            fi
        else
            fail "Voice channel creation" "API call failed"
        fi
    else
        skip "Voice channel creation" "No test category available"
    fi
    
    # Test 5: Update channel (only if chan1 exists)
    if [[ -n "$chan1_id" ]]; then
        local update_payload="{\"name\":\"${PREFIX}-text-1-updated\",\"topic\":\"Updated topic\",\"nsfw\":true,\"slowmode_delay\":3}"
        if api PUT "/api/v1/channels/$chan1_id" "$update_payload" >/dev/null; then
            pass "Channel update"
            sleep_and_get "/api/v1/channels/$chan1_id" "${PREFIX}-text-1-updated" ".channel.name" "Channel update name"
            sleep_and_get "/api/v1/channels/$chan1_id" "Updated topic" ".channel.topic" "Channel update topic"
            sleep_and_get "/api/v1/channels/$chan1_id" "true" ".channel.nsfw" "Channel update nsfw"
        else
            fail "Channel update" "API call failed"
        fi
    else
        skip "Channel update" "Channel 1 was not created successfully"
    fi
    
    # Test 6: Move channel to category
    if [[ -n "$chan1_id" && -n "$test_cat_id" && "$test_cat_id" != "null" ]]; then
        if api POST "/api/v1/guilds/$GUILD_ID/categories/$test_cat_id/channels/$chan1_id" "" >/dev/null; then
            pass "Channel move to category"
            sleep_and_get "/api/v1/channels/$chan1_id" "$test_cat_id" ".channel.category_id" "Channel move to category"
        else
            fail "Channel move to category" "API call failed"
        fi
    else
        skip "Channel move to category" "Prerequisites not met (channel or category missing)"
    fi
    
    # Test 7: List guild channels
    if api GET "/api/v1/guilds/$GUILD_ID/channels" >/dev/null; then
        pass "List guild channels"
    else
        fail "List guild channels" "API call failed"
    fi
}

# Test roles (continue on failures)
test_roles() {
    log "Testing roles..."
    
    # Get permission bits for testing
    local guild_perms_bits
    guild_perms_bits=$(get_permission_bits "CREATE_INSTANT_INVITE" "CREATE_EXPRESSIONS" "MANAGE_EXPRESSIONS")
    
    # Test 1: Create role
    local role1_payload="{\"name\":\"${PREFIX}-role-1\",\"color\":255,\"hoist\":true,\"mentionable\":true,\"permissions\":$guild_perms_bits}"
    local role1_response
    local role1_id=""
    if role1_response=$(api POST "/api/v1/guilds/$GUILD_ID/roles" "$role1_payload"); then
        role1_id=$(echo "$role1_response" | jq -r '.role.id')
        if [[ "$role1_id" != "null" && -n "$role1_id" ]]; then
            record_object "role" "$role1_id" "${PREFIX}-role-1" "null"
            pass "Role creation"
            
            sleep_and_get "/api/v1/guilds/$GUILD_ID/roles/$role1_id" "${PREFIX}-role-1" ".role.name" "Role name"
            sleep_and_get "/api/v1/guilds/$GUILD_ID/roles/$role1_id" "255" ".role.color" "Role color"
            sleep_and_get "/api/v1/guilds/$GUILD_ID/roles/$role1_id" "true" ".role.hoist" "Role hoist"
            sleep_and_get "/api/v1/guilds/$GUILD_ID/roles/$role1_id" "true" ".role.mentionable" "Role mentionable"
        else
            fail "Role creation" "Invalid role ID returned"
        fi
    else
        fail "Role creation" "API call failed"
    fi
    
    # Test 2: Update role (only if role1 exists)
    if [[ -n "$role1_id" ]]; then
        local update_payload="{\"name\":\"${PREFIX}-role-1-updated\",\"color\":65280,\"hoist\":false}"
        if api PUT "/api/v1/guilds/$GUILD_ID/roles/$role1_id" "$update_payload" >/dev/null; then
            pass "Role update"
            sleep_and_get "/api/v1/guilds/$GUILD_ID/roles/$role1_id" "${PREFIX}-role-1-updated" ".role.name" "Role update name"
            sleep_and_get "/api/v1/guilds/$GUILD_ID/roles/$role1_id" "65280" ".role.color" "Role update color"
            sleep_and_get "/api/v1/guilds/$GUILD_ID/roles/$role1_id" "false" ".role.hoist" "Role update hoist"
        else
            fail "Role update" "API call failed"
        fi
    else
        skip "Role update" "Role 1 was not created successfully"
    fi
    
    # Test 3: Assign role to user (only if role1 exists)
    if [[ -n "$role1_id" ]]; then
        if api PUT "/api/v1/guilds/$GUILD_ID/roles/$role1_id/members/$TEST_USER_ID" "" >/dev/null; then
            pass "Role assignment"
            sleep 5
            local member_response
            if member_response=$(api GET "/api/v1/guilds/$GUILD_ID/members/$TEST_USER_ID"); then
                local has_role
                has_role=$(echo "$member_response" | jq -r ".member.roles | contains([$role1_id])")
                if [[ "$has_role" == "true" ]]; then
                    pass "Role assignment verification"
                    # Record for cleanup
                    record_object "role_assignment" "$role1_id" "$TEST_USER_ID" "null"
                else
                    fail "Role assignment verification" "Role not found in user's roles"
                fi
            else
                fail "Role assignment verification" "Failed to get member details"
            fi
        else
            fail "Role assignment" "API call failed"
        fi
    else
        skip "Role assignment" "Role 1 was not created successfully"
    fi
    
    # Test 4: List guild roles
    if api GET "/api/v1/guilds/$GUILD_ID/roles" >/dev/null; then
        pass "List guild roles"
    else
        fail "List guild roles" "API call failed"
    fi
}

# Test messages (continue on failures)
test_messages() {
    log "Testing messages..."
    
    # Get a text channel for testing
    local channels_response
    local test_channel_id=""
    if channels_response=$(api GET "/api/v1/guilds/$GUILD_ID/channels"); then
        test_channel_id=$(echo "$channels_response" | jq -r ".channels[] | select(.type == \"text\" and (.name | startswith(\"${PREFIX}\"))) | .id" | head -1)
    fi
    
    if [[ -z "$test_channel_id" || "$test_channel_id" == "null" ]]; then
        skip "Message tests" "No test channel available"
        return
    fi
    
    # Test 1: Create message
    local embed_content="{\"title\":\"Test Message\",\"description\":\"This is a test message from ${PREFIX}\",\"color\":16711680}"
    local msg1_payload="{\"guild_id\":$GUILD_ID,\"channel_id\":$test_channel_id,\"content\":$embed_content}"
    local msg1_response
    local msg1_id=""
    if msg1_response=$(api POST "/api/v1/messages" "$msg1_payload"); then
        msg1_id=$(echo "$msg1_response" | jq -r '.message_id')
        if [[ "$msg1_id" != "null" && -n "$msg1_id" ]]; then
            record_object "message" "$msg1_id" "test-message-1" "$test_channel_id"
            pass "Message creation"
            
            sleep_and_get "/api/v1/messages/$GUILD_ID/$test_channel_id/$msg1_id" "Test Message" ".content.title" "Message title"
        else
            fail "Message creation" "Invalid message ID returned"
        fi
    else
        fail "Message creation" "API call failed"
    fi
    
    # Test 2: Update message (only if msg1 exists)
    if [[ -n "$msg1_id" ]]; then
        local updated_embed="{\"title\":\"Updated Test Message\",\"description\":\"This message was updated\",\"color\":65280}"
        local update_payload="{\"guild_id\":$GUILD_ID,\"channel_id\":$test_channel_id,\"message_id\":$msg1_id,\"content\":$updated_embed}"
        if api PUT "/api/v1/messages" "$update_payload" >/dev/null; then
            pass "Message update"
            sleep_and_get "/api/v1/messages/$GUILD_ID/$test_channel_id/$msg1_id" "Updated Test Message" ".content.title" "Message update title"
        else
            fail "Message update" "API call failed"
        fi
    else
        skip "Message update" "Message 1 was not created successfully"
    fi
    
    # Test 3: List channel messages
    if api GET "/api/v1/guilds/$GUILD_ID/channels/$test_channel_id/messages?limit=10" >/dev/null; then
        pass "List channel messages"
    else
        fail "List channel messages" "API call failed"
    fi
}

# Test permissions thoroughly (continue on failures)
test_permissions() {
    log "Testing permissions..."
    
    # Get test role and channel IDs
    local role_response
    local test_role_id=""
    if role_response=$(api GET "/api/v1/guilds/$GUILD_ID/roles"); then
        test_role_id=$(echo "$role_response" | jq -r ".roles[] | select(.name | startswith(\"${PREFIX}\")) | .id" | head -1)
    fi
    
    local channels_response
    local test_channel_id=""
    if channels_response=$(api GET "/api/v1/guilds/$GUILD_ID/channels"); then
        test_channel_id=$(echo "$channels_response" | jq -r ".channels[] | select(.name | startswith(\"${PREFIX}\")) | .id" | head -1)
    fi
    
    local categories_response
    local test_category_id=""
    if categories_response=$(api GET "/api/v1/guilds/$GUILD_ID/categories"); then
        test_category_id=$(echo "$categories_response" | jq -r ".categories[] | select(.name | startswith(\"${PREFIX}\")) | .id" | head -1)
    fi
    
    if [[ -z "$test_role_id" || "$test_role_id" == "null" ]]; then
        skip "Permission tests" "No test role available"
        return
    fi
    
    if [[ -z "$test_channel_id" || "$test_channel_id" == "null" ]]; then
        skip "Permission tests" "No test channel available"
        return
    fi
    
    # Get channel permission bits
    local channel_perms_bits
    channel_perms_bits=$(get_permission_bits "EMBED_LINKS" "ATTACH_FILES" "MANAGE_THREADS")
    
    # Test 1: Set direct user permission override
    local user_override="{\"allow\":$channel_perms_bits,\"deny\":0}"
    if api PUT "/api/v1/channels/$test_channel_id/permissions/$TEST_USER_ID" "$user_override" >/dev/null; then
        pass "User permission override set"
        sleep 5
        local override_response
        if override_response=$(api GET "/api/v1/channels/$test_channel_id/permissions/$TEST_USER_ID"); then
            local allow_value
            allow_value=$(echo "$override_response" | jq -r '.overwrite.allow')
            if [[ "$allow_value" == "$channel_perms_bits" ]]; then
                pass "User permission override verification"
                record_object "permission_override" "$TEST_USER_ID" "user-channel-override" "$test_channel_id"
            else
                fail "User permission override verification" "expected $channel_perms_bits, got $allow_value"
            fi
        else
            fail "User permission override verification" "Failed to get override details"
        fi
    else
        fail "User permission override set" "API call failed"
    fi
    
    # Test 2: Set role permission override
    local role_override="{\"allow\":$channel_perms_bits,\"deny\":0}"
    if api PUT "/api/v1/channels/$test_channel_id/permissions/$test_role_id" "$role_override" >/dev/null; then
        pass "Role permission override set"
        sleep 5
        local override_response
        if override_response=$(api GET "/api/v1/channels/$test_channel_id/permissions/$test_role_id"); then
            local allow_value
            allow_value=$(echo "$override_response" | jq -r '.overwrite.allow')
            if [[ "$allow_value" == "$channel_perms_bits" ]]; then
                pass "Role permission override verification"
                record_object "permission_override" "$test_role_id" "role-channel-override" "$test_channel_id"
            else
                fail "Role permission override verification" "expected $channel_perms_bits, got $allow_value"
            fi
        else
            fail "Role permission override verification" "Failed to get override details"
        fi
    else
        fail "Role permission override set" "API call failed"
    fi
    
    # Test 3: Category permission inheritance (only if category exists)
    if [[ -n "$test_category_id" && "$test_category_id" != "null" ]]; then
        local cat_overwrites="{\"overwrites\":[{\"target_id\":$TEST_USER_ID,\"type\":\"member\",\"allow\":$channel_perms_bits,\"deny\":0}]}"
        if api PUT "/api/v1/guilds/$GUILD_ID/categories/$test_category_id/permissions" "$cat_overwrites" >/dev/null; then
            pass "Category permission override set"
            sleep 5
            local cat_perms_response
            if cat_perms_response=$(api GET "/api/v1/guilds/$GUILD_ID/categories/$test_category_id/permissions"); then
                local found_override
                found_override=$(echo "$cat_perms_response" | jq -r ".overwrites[] | select(.target_id == $TEST_USER_ID and .type == \"member\") | .allow")
                if [[ "$found_override" == "$channel_perms_bits" ]]; then
                    pass "Category permission override verification"
                    record_object "permission_override" "$TEST_USER_ID" "user-category-override" "$test_category_id"
                else
                    fail "Category permission override verification" "Override not found or incorrect"
                fi
            else
                fail "Category permission override verification" "Failed to get category permissions"
            fi
        else
            fail "Category permission override set" "API call failed"
        fi
    else
        skip "Category permission tests" "No test category available"
    fi
    
    # Test 4: Check individual permissions
    for perm in "EMBED_LINKS" "ATTACH_FILES" "MANAGE_THREADS"; do
        if api GET "/api/v1/channels/$test_channel_id/permissions/$TEST_USER_ID/check?permission=$perm" >/dev/null; then
            pass "Permission check for $perm"
        else
            fail "Permission check for $perm" "API call failed"
        fi
    done
    
    # Test 5: Guild-level permission checks
    for perm in "CREATE_INSTANT_INVITE" "CREATE_EXPRESSIONS" "MANAGE_EXPRESSIONS"; do
        if api GET "/api/v1/guilds/$GUILD_ID/members/$TEST_USER_ID/permissions/check?permission=$perm" >/dev/null; then
            pass "Guild permission check for $perm"
        else
            fail "Guild permission check for $perm" "API call failed"
        fi
    done
}

# Test all remaining endpoints not covered by specific tests (continue on failures)
test_remaining_endpoints() {
    log "Testing remaining endpoints..."
    
    # Health checks
    if api GET "/api/v1/health/" >/dev/null; then
        pass "Health check endpoint"
    else
        fail "Health check endpoint" "API call failed"
    fi
    
    if api GET "/api/v1/health/simple" >/dev/null; then
        pass "Simple health check endpoint"
    else
        fail "Simple health check endpoint" "API call failed"
    fi
    
    if api GET "/api/v1/health/liveness" >/dev/null; then
        pass "Liveness check endpoint"
    else
        fail "Liveness check endpoint" "API call failed"
    fi
    
    # Root endpoint
    if api GET "/" >/dev/null; then
        pass "Root endpoint"
    else
        fail "Root endpoint" "API call failed"
    fi
    
    # List all guilds
    if api GET "/api/v1/guilds" >/dev/null; then
        pass "List guilds endpoint"
    else
        fail "List guilds endpoint" "API call failed"
    fi
    
    # Get guild details
    if api GET "/api/v1/guilds/$GUILD_ID" >/dev/null; then
        pass "Guild details endpoint"
    else
        fail "Guild details endpoint" "API call failed"
    fi
    
    # List guild members
    if api GET "/api/v1/guilds/$GUILD_ID/members" >/dev/null; then
        pass "Guild members endpoint"
    else
        fail "Guild members endpoint" "API call failed"
    fi
    
    # Get bot identity
    if api GET "/api/v1/users/@me" >/dev/null; then
        pass "Bot identity endpoint"
    else
        fail "Bot identity endpoint" "API call failed"
    fi
    
    # Get user details
    if api GET "/api/v1/users/$TEST_USER_ID" >/dev/null; then
        pass "User details endpoint"
    else
        fail "User details endpoint" "API call failed"
    fi
    
    # Get member details
    if api GET "/api/v1/guilds/$GUILD_ID/members/$TEST_USER_ID" >/dev/null; then
        pass "Member details endpoint"
    else
        fail "Member details endpoint" "API call failed"
    fi
    
    # Permission listing endpoints
    for endpoint in "/api/v1/permissions" "/api/v1/permissions/roles" "/api/v1/permissions/users" "/api/v1/permissions/channels" "/api/v1/permissions/categories"; do
        if api GET "$endpoint" >/dev/null; then
            pass "Permission list endpoint: $endpoint"
        else
            fail "Permission list endpoint: $endpoint" "API call failed"
        fi
    done
    
    # Permission conversion endpoints
    local test_names='["CREATE_INSTANT_INVITE","SEND_MESSAGES"]'
    if api POST "/api/v1/permissions/convert/names-to-value" "{\"names\":$test_names}" >/dev/null; then
        pass "Permission names-to-value conversion"
    else
        fail "Permission names-to-value conversion" "API call failed"
    fi
    
    if api POST "/api/v1/permissions/convert/value-to-names" '{"value":1}' >/dev/null; then
        pass "Permission value-to-names conversion"
    else
        fail "Permission value-to-names conversion" "API call failed"
    fi
    
    # Permission calculation
    if api POST "/api/v1/permissions/calculate" '{"base":1,"allow":2,"deny":0}' >/dev/null; then
        pass "Permission calculation"
    else
        fail "Permission calculation" "API call failed"
    fi
}

# Cleanup function (non-fatal errors)
cleanup() {
    [[ "$SKIP_CLEANUP" == "1" ]] && { log "Skipping cleanup (SKIP_CLEANUP=1)"; return 0; }
    
    log "Starting cleanup..."
    
    if [[ ! -f "$CREATED_OBJECTS_FILE" ]]; then
        log "No created objects file found, nothing to clean up"
        return 0
    fi
    
    # Read objects in reverse order for proper dependency cleanup
    local objects
    mapfile -t objects < <(tac "$CREATED_OBJECTS_FILE" 2>/dev/null || true)
    
    for obj_line in "${objects[@]}"; do
        [[ -z "$obj_line" ]] && continue
        
        local type id name parent
        type=$(echo "$obj_line" | jq -r '.type' 2>/dev/null || echo "unknown")
        id=$(echo "$obj_line" | jq -r '.id' 2>/dev/null || echo "unknown")
        name=$(echo "$obj_line" | jq -r '.name' 2>/dev/null || echo "unknown")
        parent=$(echo "$obj_line" | jq -r '.parent' 2>/dev/null || echo "null")
        
        log_detail "Cleaning up $type: $id ($name)"
        
        case "$type" in
            "message")
                api DELETE "/api/v1/messages" "{\"guild_id\":$GUILD_ID,\"channel_id\":$parent,\"message_id\":$id}" >/dev/null 2>&1 || true
                ;;
            "permission_override")
                if [[ "$parent" =~ ^[0-9]+$ ]]; then
                    api DELETE "/api/v1/channels/$parent/permissions/$id" >/dev/null 2>&1 || true
                fi
                ;;
            "role_assignment")
                api DELETE "/api/v1/guilds/$GUILD_ID/roles/$id/members/$name" >/dev/null 2>&1 || true
                ;;
            "channel")
                api DELETE "/api/v1/channels/$id" >/dev/null 2>&1 || true
                ;;
            "role")
                api DELETE "/api/v1/guilds/$GUILD_ID/roles/$id" >/dev/null 2>&1 || true
                ;;
            "category")
                api DELETE "/api/v1/guilds/$GUILD_ID/categories/$id" >/dev/null 2>&1 || true
                ;;
        esac
        
        # Small delay between deletions
        sleep 1
    done
    
    log "Cleanup completed"
}

# Trap for cleanup on exit (non-fatal)
trap 'cleanup || true' EXIT

# Main execution
main() {
    log "Starting Discord API integration tests"
    log "Run tag: $RUN_TAG"
    log "Base URL: $BASE_URL"
    log "Guild ID: $GUILD_ID"
    log "Test User ID: $TEST_USER_ID"
    log "Log file: $LOG_FILE"
    
    # Run all test suites (continue on failures)
    test_categories || true
    test_channels || true
    test_roles || true
    test_messages || true
    test_permissions || true
    test_remaining_endpoints || true
    
    # Final summary
    echo
    echo "TEST SUMMARY"
    echo "============"
    echo "PASSED: $PASSED"
    echo "FAILED: $FAILED"
    echo "SKIPPED: $SKIPPED"
    echo "TOTAL: $((PASSED + FAILED + SKIPPED))"
    echo
    echo "Log file: $LOG_FILE"
    
    if [[ $FAILED -gt 0 ]]; then
        echo
        echo "FAILED TESTS:"
        echo "============"
        printf '%s\n' "${FAILED_TESTS[@]}"
        echo
        echo "RESULT: FAIL ($FAILED failures)"
        echo "Check $LOG_FILE for detailed request/response logs"
        exit 1
    else
        echo "RESULT: PASS"
        exit 0
    fi
}

# Run main function
main "$@"