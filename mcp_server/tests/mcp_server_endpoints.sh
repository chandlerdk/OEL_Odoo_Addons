#!/bin/bash

# Test script for MCP Server endpoints based on official documentation
# This script provides comprehensive testing of all documented MCP Server endpoints
# Version: 2.0.0

# Exit on error if not in debug mode
if [ "${DEBUG:-false}" != "true" ]; then
    set -e
fi

# ====================== CONFIGURATION ======================

# Find the script directory and project root
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
# If script is in tests directory, go up one level, otherwise use current directory
if [[ "$SCRIPT_DIR" == */tests ]]; then
    PROJECT_ROOT="$( cd "$SCRIPT_DIR/.." && pwd )"
else
    PROJECT_ROOT="$SCRIPT_DIR"
fi

# Authentication Configuration
# WARNING: Use dedicated test credentials, never production credentials

# Look for .env file in the project root
ENV_FILE="$PROJECT_ROOT/.env"
if [ -f "$ENV_FILE" ]; then
    echo "Loading configuration from: $ENV_FILE"
    export $(grep -v '^#' "$ENV_FILE" | xargs)
else
    echo "ERROR: No .env file found at $ENV_FILE!"
    echo "Please create a .env file based on .env.example"
    echo "Run: cp $PROJECT_ROOT/.env.example $PROJECT_ROOT/.env"
    echo "Then update it with your test configuration"
    exit 1
fi

# Verify required variables are set
if [ -z "$ODOO_API_KEY" ]; then
    echo "ERROR: ODOO_API_KEY not set in .env file"
    exit 1
fi

# Server Configuration (AFTER loading .env)
BASE_URL="${MCP_TEST_BASE_URL:-$ODOO_URL}"
DB_NAME="${MCP_TEST_DB:-$ODOO_DB}"

# Set authentication variables
API_KEY="${MCP_TEST_API_KEY:-$ODOO_API_KEY}"
USER_ID="${MCP_TEST_USER_ID:-${ODOO_USER_ID:-2}}"
USERNAME="${MCP_TEST_USERNAME:-$ODOO_USER}"
PASSWORD="${MCP_TEST_PASSWORD:-$ODOO_PASSWORD}"

# Test Configuration
DEBUG="${DEBUG:-false}"  # Set to true for verbose output
SKIP_RATE_LIMIT_TEST="${SKIP_RATE_LIMIT_TEST:-true}"  # Rate limit test takes time
CLEANUP_TEST_DATA="${CLEANUP_TEST_DATA:-true}"  # Clean up created test records

# Model Configuration for Testing
# These should match your MCP configuration in Odoo
ENABLED_MODEL="${MCP_TEST_ENABLED_MODEL:-res.users}"
READ_ONLY_MODEL="${MCP_TEST_READ_ONLY_MODEL:-res.country}"
READ_WRITE_MODEL="${MCP_TEST_READ_WRITE_MODEL:-res.partner}"
FULL_ACCESS_MODEL="${MCP_TEST_FULL_ACCESS_MODEL:-res.company}"
DISABLED_MODEL="${MCP_TEST_DISABLED_MODEL:-ir.attachment}"

# Test Data
INVALID_API_KEY="0ef5b399e9ee9c11b053dfb6eeba8de473c29fca"
TEST_PARTNER_NAME="MCP Test Partner $(date +%s)"
TEST_COMPANY_NAME="MCP Test Company $(date +%s)"
CREATED_IDS=()  # Track created records for cleanup

# Output Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Test Counters
SUCCESS_COUNT=0
TOTAL_COUNT=0
SKIPPED_COUNT=0
FAILURES=()
TEST_GROUPS_PASSED=0
TEST_GROUPS_FAILED=0

# Timing
SCRIPT_START_TIME=$(date +%s)

echo "===================================================="
echo "       MCP Server API Test Suite v2.0.0"
echo "===================================================="
echo "Configuration:"
echo "  Base URL: $BASE_URL"
echo "  Database: $DB_NAME"
echo "  API Key: ${API_KEY:0:8}..."
echo "  User ID: $USER_ID"
echo "  Debug Mode: $DEBUG"
echo "  Skip Rate Limit Test: $SKIP_RATE_LIMIT_TEST"
echo "  Cleanup Test Data: $CLEANUP_TEST_DATA"
echo "===================================================="
echo

# ====================== HELPER FUNCTIONS ======================

# Function to safely check JSON content using jq
check_json_expr() {
    local json="$1"
    local jq_expr="$2"
    echo "$json" | jq -e "$jq_expr" > /dev/null 2>&1
    return $?
}

# Function to extract JSON value using jq
get_json_value() {
    local json="$1"
    local jq_expr="$2"
    echo "$json" | jq -r "$jq_expr" 2>/dev/null || echo ""
}

# Function to check if the response is HTML
is_html_response() {
    local response="$1"
    if echo "$response" | grep -qiE '<html|<!DOCTYPE'; then
        return 0
    else
        return 1
    fi
}

# Function to check for XML-RPC fault
is_xmlrpc_fault() {
    local response="$1"
    if echo "$response" | grep -q '<fault>'; then
        return 0
    else
        return 1
    fi
}

# Function to extract XML-RPC fault message
get_xmlrpc_fault_message() {
    local response="$1"
    echo "$response" | sed -n 's/.*<name>faultString<\/name>.*<string>\([^<]*\)<\/string>.*/\1/p' | head -1
}

# Function to extract XML-RPC result value
get_xmlrpc_result() {
    local response="$1"
    local result_type="${2:-auto}"  # auto, int, bool, string, array
    
    case "$result_type" in
        int)
            echo "$response" | sed -n 's/.*<value><int>\([0-9]\+\)<\/int><\/value>.*/\1/p' | head -1
            ;;
        bool)
            local bool_val=$(echo "$response" | sed -n 's/.*<value><boolean>\([01]\)<\/boolean><\/value>.*/\1/p' | head -1)
            if [ "$bool_val" = "1" ]; then echo "true"; else echo "false"; fi
            ;;
        string)
            echo "$response" | sed -n 's/.*<value><string>\([^<]*\)<\/string><\/value>.*/\1/p' | head -1
            ;;
        array)
            echo "$response" | grep -c '<data>' || echo "0"
            ;;
        auto)
            # Try each type in order
            local val
            val=$(get_xmlrpc_result "$response" "int")
            [ -n "$val" ] && echo "$val" && return
            
            val=$(get_xmlrpc_result "$response" "bool")
            [ -n "$val" ] && echo "$val" && return
            
            val=$(get_xmlrpc_result "$response" "string")
            [ -n "$val" ] && echo "$val" && return
            
            val=$(get_xmlrpc_result "$response" "array")
            [ "$val" != "0" ] && echo "$val" && return
            
            echo ""
            ;;
    esac
}

# Function to validate timestamp format
is_valid_timestamp() {
    local timestamp="$1"
    # Accept both formats: with microseconds or without
    if [[ "$timestamp" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{6})?$ ]]; then
        return 0
    else
        return 1
    fi
}

# Function to print test group header
print_test_group() {
    local group_name="$1"
    echo
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${CYAN}▶ $group_name${NC}"
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
}

# Test counter
TEST_COUNT=0
GROUP_SUCCESS=0
GROUP_TOTAL=0
GROUP_FAILURES=()

# Enhanced endpoint test function with better validation
test_endpoint() {
    local name="$1"
    local method="$2"
    local endpoint="$3"
    local data="$4"
    local expected_status="$5"
    local data_jq_expr="${6}"
    local error_jq_expr="${7}"
    # Handle API key parameter - distinguish between not provided and empty
    local custom_api_key
    if [ $# -lt 8 ]; then
        custom_api_key="$API_KEY"
    else
        custom_api_key="$8"
    fi
    local skip_timestamp_check="${9:-false}"

    # Increment test counter
    TEST_COUNT=$((TEST_COUNT + 1))
    GROUP_TOTAL=$((GROUP_TOTAL + 1))
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    
    echo -n -e "${BLUE}[${TEST_COUNT}]${NC} $name ... "

    # Prepare curl command
    local curl_opts=(-s -w "\n%{http_code}" -H "Accept: application/json")

    # Add API key header for non-XML-RPC endpoints
    if [[ "$endpoint" != *"/mcp/xmlrpc/"* ]] && [ -n "$custom_api_key" ]; then
        curl_opts+=(-H "X-API-Key: $custom_api_key")
    fi

    # Execute request based on method
    local start_time=$(date +%s%N)
    if [ "$method" == "GET" ]; then
        if [ -n "$data" ]; then
            RESPONSE=$(curl "${curl_opts[@]}" -X GET "$BASE_URL$endpoint?$data")
        else
            RESPONSE=$(curl "${curl_opts[@]}" -X GET "$BASE_URL$endpoint")
        fi
    elif [ "$method" == "POST" ]; then
        local content_type="application/json"
        if [[ "$endpoint" == *"/xmlrpc/"* ]]; then
            content_type="text/xml"
        fi
        curl_opts+=(-H "Content-Type: $content_type")
        RESPONSE=$(curl "${curl_opts[@]}" -X POST -d "$data" "$BASE_URL$endpoint")
    else
        curl_opts+=(-X "$method")
        RESPONSE=$(curl "${curl_opts[@]}" "$BASE_URL$endpoint")
    fi
    local end_time=$(date +%s%N)
    local duration=$(( (end_time - start_time) / 1000000 ))

    # Split response and status code
    HTTP_BODY=$(echo "$RESPONSE" | sed '$d')
    HTTP_STATUS=$(echo "$RESPONSE" | tail -n 1)

    # Validate response
    local test_passed=false
    local failure_reason=""

    # Handle 405 Method Not Allowed
    if [ "$HTTP_STATUS" -eq 405 ] && [ "$expected_status" -eq 405 ]; then
        if is_html_response "$HTTP_BODY"; then
            test_passed=true
        else
            failure_reason="Expected HTML 405 response"
        fi
    # Handle XML-RPC responses
    elif [[ "$endpoint" == *"/mcp/xmlrpc/"* ]]; then
        if [ "$HTTP_STATUS" -eq "$expected_status" ]; then
            if [ "$expected_status" -eq 200 ]; then
                if is_xmlrpc_fault "$HTTP_BODY"; then
                    if [ -n "$error_jq_expr" ]; then
                        # Expected fault
                        test_passed=true
                    else
                        failure_reason="Unexpected XML-RPC fault: $(get_xmlrpc_fault_message "$HTTP_BODY")"
                    fi
                else
                    # Expected success
                    test_passed=true
                fi
            else
                test_passed=true
            fi
        else
            failure_reason="Expected status $expected_status, got $HTTP_STATUS"
        fi
    # Handle JSON responses
    elif [ "$HTTP_STATUS" -eq "$expected_status" ]; then
        if echo "$HTTP_BODY" | jq . > /dev/null 2>&1; then
            # Validate response structure
            if [[ "$HTTP_STATUS" -ge 200 && "$HTTP_STATUS" -lt 300 ]]; then
                # Success response validation
                if check_json_expr "$HTTP_BODY" '.success == true'; then
                    # Check timestamp if not skipped
                    if [ "$skip_timestamp_check" != "true" ]; then
                        local timestamp=$(get_json_value "$HTTP_BODY" '.meta.timestamp')
                        if ! is_valid_timestamp "$timestamp"; then
                            failure_reason="Invalid or missing timestamp"
                        fi
                    fi
                    
                    # Validate data if expression provided
                    if [ -z "$failure_reason" ] && [ -n "$data_jq_expr" ]; then
                        if ! check_json_expr "$HTTP_BODY" ".data | $data_jq_expr"; then
                            failure_reason="Data validation failed"
                        fi
                    fi
                    
                    [ -z "$failure_reason" ] && test_passed=true
                else
                    failure_reason="Response missing success=true"
                fi
            else
                # Error response validation
                if check_json_expr "$HTTP_BODY" '.success == false'; then
                    if [ -n "$error_jq_expr" ]; then
                        if check_json_expr "$HTTP_BODY" "$error_jq_expr"; then
                            test_passed=true
                        else
                            failure_reason="Error validation failed"
                        fi
                    else
                        test_passed=true
                    fi
                else
                    failure_reason="Error response missing success=false"
                fi
            fi
        else
            failure_reason="Invalid JSON response"
        fi
    else
        failure_reason="Expected status $expected_status, got $HTTP_STATUS"
    fi

    # Record result
    if $test_passed; then
        echo -e "${GREEN}✓${NC} (${duration}ms)"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        GROUP_SUCCESS=$((GROUP_SUCCESS + 1))
    else
        echo -e "${RED}✗${NC} - $failure_reason"
        FAILURES+=("Test ${TEST_COUNT}: $name - $failure_reason")
        GROUP_FAILURES+=("$name - $failure_reason")
        if [ "$DEBUG" = "true" ]; then
            echo -e "${YELLOW}Debug - Response:${NC}"
            echo "$HTTP_BODY" | jq . 2>/dev/null || echo "$HTTP_BODY" | head -n 10
            echo
        fi
    fi
}

# Enhanced XML-RPC test function
test_xmlrpc() {
    local name="$1"
    local method="$2"
    local model="$3"
    local args_xml="$4"
    local expect_fault="$5"
    local alt_api_key="${6:-$API_KEY}"
    local capture_result_var="${7:-}"
    local kwargs_xml="${8:-}"  # Optional kwargs parameter

    # Increment test counter
    TEST_COUNT=$((TEST_COUNT + 1))
    GROUP_TOTAL=$((GROUP_TOTAL + 1))
    TOTAL_COUNT=$((TOTAL_COUNT + 1))
    
    echo -n -e "${BLUE}[${TEST_COUNT}]${NC} $name ... "

    # Build kwargs parameter if provided
    local kwargs_param=""
    if [ -n "$kwargs_xml" ]; then
        kwargs_param="<param><value>$kwargs_xml</value></param>"
    fi

    # Generate the XML-RPC request
    local XML_REQUEST=$(cat <<EOF
<?xml version="1.0"?>
<methodCall>
<methodName>execute_kw</methodName>
<params>
<param><value><string>$DB_NAME</string></value></param>
<param><value><int>$USER_ID</int></value></param>
<param><value><string>$alt_api_key</string></value></param>
<param><value><string>$model</string></value></param>
<param><value><string>$method</string></value></param>
<param><value><array><data>
$args_xml
</data></array></value></param>
$kwargs_param
</params>
</methodCall>
EOF
)

    if [ "$DEBUG" = "true" ]; then
        echo -e "\n${YELLOW}XML Request:${NC}"
        echo "$XML_REQUEST" | head -n 20
    fi

    # Execute request with timing
    local start_time=$(date +%s%N)
    RESPONSE=$(curl -s -X POST -w "\n%{http_code}" -H "Content-Type: text/xml" --data "$XML_REQUEST" "$BASE_URL/mcp/xmlrpc/object")
    local end_time=$(date +%s%N)
    local duration=$(( (end_time - start_time) / 1000000 ))

    # Split response and status code
    HTTP_BODY=$(echo "$RESPONSE" | sed '$d')
    HTTP_STATUS=$(echo "$RESPONSE" | tail -n 1)

    # Validate response
    local test_passed=false
    local failure_reason=""

    if [ "$HTTP_STATUS" -ne 200 ]; then
        failure_reason="Expected status 200, got $HTTP_STATUS"
    else
        if is_xmlrpc_fault "$HTTP_BODY"; then
            if [ "$expect_fault" = true ]; then
                test_passed=true
                local fault_msg=$(get_xmlrpc_fault_message "$HTTP_BODY")
                [ "$DEBUG" = "true" ] && echo -e "\n${YELLOW}Expected fault: $fault_msg${NC}"
            else
                failure_reason="Unexpected fault: $(get_xmlrpc_fault_message "$HTTP_BODY")"
            fi
        else
            if [ "$expect_fault" = true ]; then
                failure_reason="Expected fault but got success"
            else
                test_passed=true
                # Capture result if requested
                if [ -n "$capture_result_var" ]; then
                    local result_value=$(get_xmlrpc_result "$HTTP_BODY")
                    printf -v "$capture_result_var" "%s" "$result_value"
                    [ "$DEBUG" = "true" ] && echo -e "\n${YELLOW}Captured: $capture_result_var=$result_value${NC}"
                fi
            fi
        fi
    fi

    # Record result
    if $test_passed; then
        echo -e "${GREEN}✓${NC} (${duration}ms)"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
        GROUP_SUCCESS=$((GROUP_SUCCESS + 1))
    else
        echo -e "${RED}✗${NC} - $failure_reason"
        FAILURES+=("Test ${TEST_COUNT}: $name - $failure_reason")
        GROUP_FAILURES+=("$name - $failure_reason")
        if [ "$DEBUG" = "true" ]; then
            echo -e "${YELLOW}Debug - Response:${NC}"
            echo "$HTTP_BODY" | head -n 20
            echo
        fi
    fi
}

# Function to finish a test group
finish_test_group() {
    if [ $GROUP_TOTAL -gt 0 ]; then
        if [ $GROUP_SUCCESS -eq $GROUP_TOTAL ]; then
            echo -e "\n${GREEN}✓ Group passed: $GROUP_SUCCESS/$GROUP_TOTAL tests${NC}"
            TEST_GROUPS_PASSED=$((TEST_GROUPS_PASSED + 1))
        else
            echo -e "\n${RED}✗ Group failed: $GROUP_SUCCESS/$GROUP_TOTAL tests passed${NC}"
            TEST_GROUPS_FAILED=$((TEST_GROUPS_FAILED + 1))
            if [ ${#GROUP_FAILURES[@]} -gt 0 ]; then
                echo -e "${YELLOW}Failed tests in this group:${NC}"
                for failure in "${GROUP_FAILURES[@]}"; do
                    echo "  - $failure"
                done
            fi
        fi
        GROUP_SUCCESS=0
        GROUP_TOTAL=0
        GROUP_FAILURES=()
    fi
}

# Function to skip a test
skip_test() {
    local reason="$1"
    SKIPPED_COUNT=$((SKIPPED_COUNT + 1))
    echo -e "${YELLOW}⚠ Skipped: $reason${NC}"
}

# Function to test rate limiting
test_rate_limiting() {
    if [ "$SKIP_RATE_LIMIT_TEST" = "true" ]; then
        skip_test "Rate limiting test (set SKIP_RATE_LIMIT_TEST=false to enable)"
        return
    fi

    echo -e "${BLUE}Testing rate limiting (this may take a minute)...${NC}"
    local requests_made=0
    local rate_limited=false
    
    # Make 301 requests rapidly (limit is 300/min)
    for i in {1..301}; do
        RESPONSE=$(curl -s -w "%{http_code}" -o /dev/null -H "X-API-Key: $API_KEY" "$BASE_URL/mcp/health")
        requests_made=$((requests_made + 1))
        
        if [ "$RESPONSE" = "429" ]; then
            rate_limited=true
            break
        fi
        
        # Show progress every 50 requests
        if [ $((i % 50)) -eq 0 ]; then
            echo -n "."
        fi
    done
    
    echo
    
    if $rate_limited; then
        echo -e "${GREEN}✓${NC} Rate limiting working (limited after $requests_made requests)"
        SUCCESS_COUNT=$((SUCCESS_COUNT + 1))
    else
        echo -e "${RED}✗${NC} Rate limiting not triggered after 301 requests"
        FAILURES+=("Rate limiting test - Not triggered after 301 requests")
    fi
    
    # Wait for rate limit to reset
    echo "Waiting 60 seconds for rate limit to reset..."
    sleep 60
}

# ====================== TEST EXECUTION ======================

# Test 1: Basic Health Check
print_test_group "1. Health Check Endpoint"

test_endpoint "Health Check - GET" \
    "GET" "/mcp/health" "" 200 \
    '.status == "ok" and .mcp_server_version != null'

test_endpoint "Health Check - POST (Method Not Allowed)" \
    "POST" "/mcp/health" "" 405

test_endpoint "Health Check - PUT (Method Not Allowed)" \
    "PUT" "/mcp/health" "" 405

test_endpoint "Health Check - DELETE (Method Not Allowed)" \
    "DELETE" "/mcp/health" "" 405

finish_test_group

# Test 2: Authentication
print_test_group "2. Authentication Endpoints"

test_endpoint "Validate API Key - Valid" \
    "GET" "/mcp/auth/validate" "" 200 \
    '.valid == true and .user_id > 0'

test_endpoint "Validate API Key - Invalid" \
    "GET" "/mcp/auth/validate" "" 401 \
    "" '.error.code == "E401" and (.error.message | contains("Invalid or missing API key"))' \
    "$INVALID_API_KEY"

test_endpoint "Validate API Key - Empty Header" \
    "GET" "/mcp/auth/validate" "" 401 \
    "" '.error.code == "E401"' \
    ""

finish_test_group

# Test 3: System Information
print_test_group "3. System Information"

test_endpoint "System Info - Authorized" \
    "GET" "/mcp/system/info" "" 200 \
    '.db_name == "'$DB_NAME'" and .odoo_version != null and .enabled_mcp_models >= 0 and .mcp_server_version != null and .server_timezone != null'

test_endpoint "System Info - Unauthorized" \
    "GET" "/mcp/system/info" "" 401 \
    "" '.error.code == "E401"' \
    "$INVALID_API_KEY"

finish_test_group

# Test 4: Model Management
print_test_group "4. Model Management Endpoints"

test_endpoint "Get Enabled Models - Authorized" \
    "GET" "/mcp/models" "" 200 \
    '(.models | type) == "array" and (.models | length) > 0'

test_endpoint "Get Enabled Models - Unauthorized" \
    "GET" "/mcp/models" "" 401 \
    "" '.error.code == "E401"' \
    "$INVALID_API_KEY"

test_endpoint "Model Access - Read/Write Model" \
    "GET" "/mcp/models/$READ_WRITE_MODEL/access" "" 200 \
    '.model == "'$READ_WRITE_MODEL'" and .enabled == true and .operations.read == true and .operations.write == true'

test_endpoint "Model Access - Read Only Model" \
    "GET" "/mcp/models/$READ_ONLY_MODEL/access" "" 200 \
    '.model == "'$READ_ONLY_MODEL'" and .enabled == true and .operations.read == true and .operations.write == false'

test_endpoint "Model Access - Full Access Model" \
    "GET" "/mcp/models/$FULL_ACCESS_MODEL/access" "" 200 \
    '.model == "'$FULL_ACCESS_MODEL'" and .enabled == true and .operations.read == true and .operations.write == true and .operations.create == true and .operations.unlink == true'

test_endpoint "Model Access - Disabled Model" \
    "GET" "/mcp/models/$DISABLED_MODEL/access" "" 403 \
    "" '.error.code == "E403" and (.error.message | contains("not enabled"))'

test_endpoint "Model Access - Invalid Model Name" \
    "GET" "/mcp/models/invalid.model.name/access" "" 404 \
    "" '.error.code == "E404"'

finish_test_group

# Test 5: XML-RPC Common Interface
print_test_group "5. XML-RPC Common Interface"

test_endpoint "XML-RPC Common - Version" \
    "POST" "/mcp/xmlrpc/common" \
    '<?xml version="1.0"?>
<methodCall>
  <methodName>version</methodName>
  <params></params>
</methodCall>' 200

# Test authentication via XML-RPC
test_endpoint "XML-RPC Common - Authenticate" \
    "POST" "/mcp/xmlrpc/common" \
    '<?xml version="1.0"?>
<methodCall>
  <methodName>authenticate</methodName>
  <params>
    <param><value><string>'$DB_NAME'</string></value></param>
    <param><value><string>'$USERNAME'</string></value></param>
    <param><value><string>'$PASSWORD'</string></value></param>
    <param><value><struct></struct></value></param>
  </params>
</methodCall>' 200

finish_test_group

# Test 6: XML-RPC Database Interface
print_test_group "6. XML-RPC Database Interface"

test_endpoint "XML-RPC DB - List Databases" \
    "POST" "/mcp/xmlrpc/db" \
    '<?xml version="1.0"?>
<methodCall>
  <methodName>list</methodName>
  <params></params>
</methodCall>' 200

test_endpoint "XML-RPC DB - Server Version" \
    "POST" "/mcp/xmlrpc/db" \
    '<?xml version="1.0"?>
<methodCall>
  <methodName>server_version</methodName>
  <params></params>
</methodCall>' 200

finish_test_group

# Test 7: XML-RPC Object Interface - Read Operations
print_test_group "7. XML-RPC Object Interface - Read Operations"

# Search operation
test_xmlrpc "Search Partners" \
    "search" "$READ_WRITE_MODEL" \
    '<value><array><data>
        <value><array><data>
            <value><string>is_company</string></value>
            <value><string>=</string></value>
            <value><boolean>1</boolean></value>
        </data></array></value>
    </data></array></value>' \
    false "$API_KEY" "PARTNER_IDS" \
    '<struct>
        <member><name>limit</name><value><int>5</int></value></member>
    </struct>'

# Read operation
test_xmlrpc "Read Partner Details" \
    "read" "$READ_WRITE_MODEL" \
    '<value><array><data><value><int>1</int></value></data></array></value>' \
    false "$API_KEY" "" \
    '<struct>
        <member><name>fields</name><value><array><data>
            <value><string>name</string></value>
            <value><string>email</string></value>
        </data></array></value></member>
    </struct>'

# Search-read operation
test_xmlrpc "Search-Read Partners" \
    "search_read" "$READ_WRITE_MODEL" \
    '<value><array><data></data></array></value>' \
    false "$API_KEY" "" \
    '<struct>
        <member><name>fields</name><value><array><data>
            <value><string>id</string></value>
            <value><string>name</string></value>
        </data></array></value></member>
        <member><name>limit</name><value><int>3</int></value></member>
    </struct>'

# Search count operation
test_xmlrpc "Count Partners" \
    "search_count" "$READ_WRITE_MODEL" \
    '<value><array><data>
        <value><array><data>
            <value><string>is_company</string></value>
            <value><string>=</string></value>
            <value><boolean>1</boolean></value>
        </data></array></value>
    </data></array></value>' \
    false

# Fields get operation
test_xmlrpc "Get Partner Fields" \
    "fields_get" "$READ_WRITE_MODEL" \
    '<value><array><data></data></array></value>' \
    false "$API_KEY" "" \
    '<struct>
        <member><name>attributes</name><value><array><data>
            <value><string>string</string></value>
            <value><string>type</string></value>
            <value><string>required</string></value>
        </data></array></value></member>
    </struct>'

# Name search operation
test_xmlrpc "Name Search Partners" \
    "name_search" "$READ_WRITE_MODEL" \
    '<value><string></string></value>' \
    false "$API_KEY" "" \
    '<struct>
        <member><name>limit</name><value><int>10</int></value></member>
    </struct>'

finish_test_group

# Test 8: XML-RPC Object Interface - Write Operations
print_test_group "8. XML-RPC Object Interface - Write Operations"

# Test write on read-only model (should fail)
test_xmlrpc "Write on Read-Only Model (Should Fail)" \
    "write" "$READ_ONLY_MODEL" \
    '<value><array><data><value><int>1</int></value></data></array></value>
     <value><struct>
        <member><name>name</name><value><string>Test Update</string></value></member>
     </struct></value>' \
    true

# Test write on read-write model (should succeed if record exists)
test_xmlrpc "Write on Read-Write Model" \
    "write" "$READ_WRITE_MODEL" \
    '<value><array><data><value><int>1</int></value></data></array></value>
     <value><struct>
        <member><name>comment</name><value><string>Updated by MCP test</string></value></member>
     </struct></value>' \
    false

finish_test_group

# Test 9: XML-RPC Object Interface - Create Operations
print_test_group "9. XML-RPC Object Interface - Create Operations"

# Test create on read-only model (should fail)
test_xmlrpc "Create on Read-Only Model (Should Fail)" \
    "create" "$READ_ONLY_MODEL" \
    '<value><struct>
        <member><name>name</name><value><string>Test Country</string></value></member>
        <member><name>code</name><value><string>TC</string></value></member>
    </struct></value>' \
    true

# Test create on full access model (should succeed)
test_xmlrpc "Create on Full Access Model" \
    "create" "$FULL_ACCESS_MODEL" \
    "<value><struct>
        <member><name>name</name><value><string>$TEST_COMPANY_NAME</string></value></member>
    </struct></value>" \
    false "$API_KEY" "CREATED_COMPANY_ID"

# Track created ID for cleanup
if [ -n "$CREATED_COMPANY_ID" ] && [ "$CREATED_COMPANY_ID" != "" ] && [ "$CREATED_COMPANY_ID" != "false" ]; then
    CREATED_IDS+=("$FULL_ACCESS_MODEL:$CREATED_COMPANY_ID")
    
    # Test copy operation only if we have a valid ID
    test_xmlrpc "Copy Record" \
        "copy" "$FULL_ACCESS_MODEL" \
        "<value><int>$CREATED_COMPANY_ID</int></value>" \
        false "$API_KEY" "COPIED_COMPANY_ID"
    
    if [ -n "$COPIED_COMPANY_ID" ] && [ "$COPIED_COMPANY_ID" != "" ] && [ "$COPIED_COMPANY_ID" != "false" ]; then
        CREATED_IDS+=("$FULL_ACCESS_MODEL:$COPIED_COMPANY_ID")
    fi
else
    skip_test "Copy Record - No valid ID from create operation"
fi

finish_test_group

# Test 10: XML-RPC Object Interface - Delete Operations
print_test_group "10. XML-RPC Object Interface - Delete Operations"

# Test unlink on read-write model (should fail - no unlink permission)
test_xmlrpc "Unlink on Read-Write Model (Should Fail)" \
    "unlink" "$READ_WRITE_MODEL" \
    '<value><array><data><value><int>999999</int></value></data></array></value>' \
    true

# Test unlink on full access model (cleanup created records)
if [ ${#CREATED_IDS[@]} -gt 0 ] && [ "$CLEANUP_TEST_DATA" = "true" ]; then
    for record in "${CREATED_IDS[@]}"; do
        IFS=':' read -r model id <<< "$record"
        if [ "$model" = "$FULL_ACCESS_MODEL" ] && [ -n "$id" ] && [ "$id" != "false" ] && [ "$id" != "" ]; then
            test_xmlrpc "Cleanup - Delete Created Record ($model:$id)" \
                "unlink" "$model" \
                "<value><array><data><value><int>$id</int></value></data></array></value>" \
                false
        fi
    done
fi

finish_test_group

# Test 11: Access Control and Error Handling
print_test_group "11. Access Control and Error Handling"

# Test with invalid API key
test_xmlrpc "Invalid API Key" \
    "search" "$READ_WRITE_MODEL" \
    '<value><array><data></data></array></value>' \
    true "$INVALID_API_KEY"

# Test disabled model
test_xmlrpc "Access Disabled Model" \
    "search" "$DISABLED_MODEL" \
    '<value><array><data></data></array></value>' \
    true

# Test non-existent model
test_xmlrpc "Non-existent Model" \
    "search" "non.existent.model" \
    '<value><array><data></data></array></value>' \
    true

# Test unmapped operation
test_xmlrpc "Unmapped Operation" \
    "button_action" "$READ_WRITE_MODEL" \
    '<value><array><data></data></array></value>' \
    true

finish_test_group

# Test 12: Rate Limiting
print_test_group "12. Rate Limiting"
test_rate_limiting
finish_test_group

# ====================== TEST SUMMARY ======================

echo
echo "===================================================="
echo "              TEST EXECUTION SUMMARY"
echo "===================================================="

# Calculate final statistics
# Count actual failures (excluding skipped tests)
FAIL_COUNT=${#FAILURES[@]}
SCRIPT_END_TIME=$(date +%s)
TOTAL_DURATION=$((SCRIPT_END_TIME - SCRIPT_START_TIME))
MINUTES=$((TOTAL_DURATION / 60))
SECONDS=$((TOTAL_DURATION % 60))

# Summary statistics
echo -e "${CYAN}Test Statistics:${NC}"
echo -e "  Total Tests:     $TOTAL_COUNT"
echo -e "  Passed:          ${GREEN}$SUCCESS_COUNT${NC}"
echo -e "  Failed:          ${RED}$FAIL_COUNT${NC}"
echo -e "  Skipped:         ${YELLOW}$SKIPPED_COUNT${NC}"
if [ $TOTAL_COUNT -gt 0 ]; then
    SUCCESS_RATE=$(( (SUCCESS_COUNT * 100) / TOTAL_COUNT ))
    echo -e "  Success Rate:    ${GREEN}$SUCCESS_RATE%${NC}"
fi
echo

echo -e "${CYAN}Test Groups:${NC}"
echo -e "  Groups Passed:   ${GREEN}$TEST_GROUPS_PASSED${NC}"
echo -e "  Groups Failed:   ${RED}$TEST_GROUPS_FAILED${NC}"
echo

echo -e "${CYAN}Execution Time:${NC}"
echo -e "  Duration:        ${MINUTES}m ${SECONDS}s"
echo

# Show failure details if any
if [ $FAIL_COUNT -gt 0 ]; then
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${RED}FAILED TESTS:${NC}"
    echo -e "${RED}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    for failure in "${FAILURES[@]}"; do
        echo -e "  ${RED}✗${NC} $failure"
    done
    echo
fi

# Final status
echo "===================================================="
if [ $FAIL_COUNT -eq 0 ]; then
    echo -e "${GREEN}✅ ALL TESTS PASSED!${NC}"
    echo "===================================================="
    exit 0
else
    if [ $SUCCESS_RATE -ge 80 ]; then
        echo -e "${YELLOW}⚠️  TESTS COMPLETED WITH WARNINGS${NC}"
        echo -e "${YELLOW}   Most tests passed ($SUCCESS_RATE%), but some failures detected.${NC}"
    else
        echo -e "${RED}❌ TESTS FAILED${NC}"
        echo -e "${RED}   Multiple test failures detected ($SUCCESS_RATE% success rate).${NC}"
    fi
    echo "===================================================="
    exit 1
fi