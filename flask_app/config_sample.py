# (optional) Filter devices by type (roomdesk, phone, accessory, webexgo, unknown)
DEVICE_TYPE = ""

# Max Period (in days) to keep call history, Frequency to gather call history from devices (minutes)
CALL_HISTORY_MAX_PERIOD = 60
CALL_HISTORY_REFRESH_CYCLE = 10

# ServiceNow Functionality (Open Incident Page, Closed Incident Page)
SERVICE_NOW_FEATURE = False
INCLUDE_ENDPOINT_NAME = False

# ServiceNow Filters (controls which incidents are returned to the dashboard - by default: active tickets (open),
# last 48 hours (closed) For all available filter parameters, build filter with UI, right click query string,
# copy query to sysparm_query
OPEN_SERVICE_NOW_INCIDENT_FILTER = {"sysparm_display_value": "true", "sysparm_query": "active=true^category=devices"}

LAST_X_HOURS = 48 # Last 48 hours of closed tickets by default
CLOSED_SERVICE_NOW_INCIDENT_FILTER = {"sysparm_display_value": "true", "sysparm_query": "active=false^category=devices"}
