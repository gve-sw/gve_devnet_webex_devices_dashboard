#!/usr/bin/env python3
"""
Copyright (c) 2024 Cisco and/or its affiliates.
This software is licensed to you under the terms of the Cisco Sample
Code License, Version 1.1 (the "License"). You may obtain a copy of the
License at
https://developer.cisco.com/docs/licenses
All use of the material herein must be in accordance with the terms of
the License. All rights not expressly granted by the License are
reserved. Unless required by applicable law or agreed to separately in
writing, software distributed under the License is distributed on an "AS
IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express
or implied.
"""

__author__ = "Trevor Maco <tmaco@cisco.com>"
__copyright__ = "Copyright (c) 2024 Cisco and/or its affiliates."
__license__ = "Cisco Sample Code License, Version 1.1"

import copy
import json
import os
import sqlite3
from datetime import datetime, timedelta
from io import BytesIO

import pandas as pd
import pytz
import requests
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, render_template, request, Response, g, redirect, url_for, jsonify

import config
import db
import util
from servicenow import ServiceNow
from webex import WebexDeviceAPI, get_webex_token

# Absolute Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(script_dir, 'db/sqlite.db')

# Global variables
app = Flask(__name__)
app.config['DATABASE'] = db_path

# Set up file and console logging
logger = util.set_up_logging()
logger_background = util.set_up_logging_background()

# Get Valid Webex Access Token
access_token = get_webex_token(logger)

# Get instance of Device API Class (Main and Periodic Background Thread)
device_api = WebexDeviceAPI(access_token, logger)
device_api_background = WebexDeviceAPI(access_token, logger_background)

# Define Global Class Object (contains all API methods for SNOW)
snow = ServiceNow(logger)

# Background Scheduler, used to periodically query Webex Devices for 30 day calling history
scheduler = BackgroundScheduler()

# Room id for MOSS Value Alert Room (if feature is enabled)
room_id = None


# Methods
def getSystemTimeAndLocation() -> str:
    """
    Return location and time of accessing device (used on all webpage footers)
    :return:
    """
    # request user ip
    userIPRequest = requests.get('https://get.geojs.io/v1/ip.json')
    userIP = userIPRequest.json()['ip']

    # request geo information based on ip
    geoRequestURL = 'https://get.geojs.io/v1/ip/geo/' + userIP + '.json'
    geoRequest = requests.get(geoRequestURL)
    geoData = geoRequest.json()

    # create info string
    location = geoData['country']
    timezone = pytz.timezone(geoData['timezone'])

    current_time = datetime.now(timezone).strftime("%d %b %Y, %I:%M %p")
    timeAndLocation = "System Information: {}, {} (Timezone: {})".format(location, current_time, timezone)

    return timeAndLocation


def get_devices_periodically(api: WebexDeviceAPI):
    """
    Get Webex Devices periodically and update the DB (runs every 5 minutes by default)
    :param api: WebexDeviceAPI instance - get all devices, add to DB in the background)
    """
    # Connection to DB (one-time)
    conn = db.create_connection(app.config['DATABASE'])

    # Get Device List
    devices = api.get_all_devices(config.DEVICE_TYPE)

    # Clear devices from table not returned in devices api call (no longer have xapi permission, removed, etc.)
    new_device_ids = [device['id'] for device in devices]
    current_device_ids = db.query_all_devices(conn, "device_id")
    for device_id in current_device_ids:
        if device_id[0] not in new_device_ids:
            db.delete_old_device_entries(conn, device_id[0])

    # Add devices to db
    for device in devices:
        # Enrich Device Details
        device = enrich_device_fields(device)

        # Add device to db
        db.add_device_entries(conn, device)

    db.close_connection(conn)


def get_device_call_history_periodically(api: WebexDeviceAPI):
    """
    Get All Call history across all Webex Devices in the background (every X minutes, only retain calls younger than X days - both configured in config.py)
    :param api: WebexDeviceAPI used to get call history for all devices, add to DB table
    """
    # Connection to DB (one-time)
    conn = db.create_connection(app.config['DATABASE'])

    # Get Device ID's List
    device_ids = db.query_all_devices(conn, "device_id")
    device_ids = [device_id[0] for device_id in device_ids]

    # Get Call History for all devices
    call_history = api.get_call_history(device_ids)

    # Calculate x days ago (ensure only historical entries within x days saved)
    x_days_ago = datetime.now(pytz.utc) - timedelta(days=config.CALL_HISTORY_MAX_PERIOD)

    for device_id in call_history:
        db.add_history_entries(conn, x_days_ago, call_history[device_id])

    # Delete all entries older than 30 days (cleanup)
    db.delete_old_call_entries(conn, x_days_ago)

    # Close connection to DB
    db.close_connection(conn)


def enrich_device_fields(device: dict) -> dict:
    """
    Obtains additional fields and information about each device for webpage display (ex: location name given a location id)
    :param device: Specific device, originally containing only native API fields
    :return: "Enriched" device dictionary, additional fields added for further processing and displaying on dashboard
    """
    # Get Device Location Name (if possible)
    if 'locationId' in device:
        location_details = device_api.get_location_details(device['locationId'])
        device['site'] = location_details.get('name', 'Unknown')
        device['timeZone'] = location_details.get('timeZone', 'N/A')
    else:
        device['site'] = 'Unknown'
        device['timeZone'] = 'N/A'

    # Display friendly status
    device['connectionStatus'] = device['connectionStatus'].capitalize()

    if device['connectionStatus'] == 'Connected_with_issues':
        device['connectionStatus'] = "Issues"
    elif device['connectionStatus'] == 'Offline_expired':
        device['connectionStatus'] = "Offline Expired"

    # Determine 'mode' (and Workspace name if relevant)
    if 'workspaceId' in device:
        device['mode'] = 'Shared'

        # Get workspace name
        workspace_details = device_api.get_workspace_details(device['workspaceId'])

        if workspace_details:
            device['room'] = ''
            device['email'] = ''

            if 'displayName' in workspace_details:
                device['room'] = workspace_details['displayName']

            # Get mailbox info (if integration enabled and assigned)
            if 'calendar' in workspace_details and workspace_details['calendar']['type'] != 'none':
                device['email'] = workspace_details['calendar'].get('emailAddress', "Unknown")
        else:
            device['room'] = ''
            device['email'] = ''
    else:
        # Personal mode
        device['mode'] = 'Personal'
        device['room'] = ''
        device['email'] = ''

    # Get Uptime
    system_unit_information = device_api.get_system_unit_information(device['id'])
    if 'Uptime' in system_unit_information:
        uptime = system_unit_information['Uptime']
        device['uptime'] = util.convert_seconds_to_time(uptime)
    else:
        device['uptime'] = 'Unknown'

    return device


def lookup_device_details(api: WebexDeviceAPI, device_id: str) -> dict:
    """
    Get up-to-date Webex Device information details (from API), write to DB. Ensures most up-to-date info when clicking into a device on the dashboard - runs ad hoc)
    :param api: WebexDeviceAPI, used to get device details from API
    :param device_id: Specific device id, used to update device with new information on DB (if required)
    :return: Device dictionary - most up-to-date info
    """
    # Connection to DB (one-time)
    conn = db.create_connection(app.config['DATABASE'])

    # Raw Device Details
    device = api.get_device_details(device_id)

    # Enrich Device Details
    device = enrich_device_fields(device)

    # Add device to db
    db.add_device_entries(conn, device)

    # Close connection to DB
    db.close_connection(conn)

    return device


def get_system_unit_information(device_id: str, device: dict) -> dict:
    """
    Get System Unit Information for Webex Device, enrich with additional fields for display
    :param device_id: Unique Webex Device ID
    :param device: Raw device information dictionary
    :return: Enriched system unit information for dashboard display
    """
    system_unit = {'site': device['site'], 'ip': device['ip']}

    # Get System Unit Information
    system_unit_information = device_api.get_system_unit_information(device_id)

    system_unit['type'] = system_unit_information.get('ProductType', '')
    system_unit['product'] = system_unit_information.get('ProductPlatform', '')

    if 'Hardware' in system_unit_information:
        hardware_info = system_unit_information['Hardware']

        system_unit['hw_mod_serial'] = hardware_info['Module'].get('SerialNumber', '')
        system_unit['hw_comp_level'] = hardware_info['Module'].get('CompatibilityLevel', '')

    if 'Software' in system_unit_information:
        software_info = system_unit_information['Software']

        system_unit['sw_name'] = software_info.get('Name', '')
        system_unit['sw_ver'] = software_info.get('Version', '')
        system_unit['sw_release'] = software_info.get('ReleaseDate', '')

    # Boot Time
    if 'Uptime' in system_unit_information:
        uptime = system_unit_information['Uptime']
        system_unit['boot_time'] = f"{util.calculate_start_time(uptime, device['timeZone'])} ({uptime} seconds)"

    return system_unit


def get_room_analytics(device_id: str) -> dict:
    """
    Get Room Analytics Information for Webex Device, enrich with additional fields for display
    :param device_id: Unique Webex Device id
    :return: Dictionary of enriched Room Analytics information for device and dashboard display
    """
    room_analytics = {}

    # Get People Presence
    analytics = device_api.get_room_analytics(device_id)

    room_analytics['people_present'] = analytics.get('PeoplePresence', 'N/A')

    # People Count (Current / Capacity)
    if 'PeopleCount' in analytics:
        room_analytics['people_count'] = f"{analytics['PeopleCount']['Current']}/{analytics['PeopleCount']['Capacity']}"
    else:
        room_analytics['people_count'] = 'N/A'

    # Get Audio Information (Mics and Speakers)
    audio = device_api.get_audio_information(device_id)

    if 'Microphones' in audio:
        room_analytics['mic_muted'] = audio['Microphones']['Mute']
        room_analytics['speaker_volume'] = audio['Volume']
    else:
        room_analytics['mic_muted'] = 'N/A'
        room_analytics['speaker_volume'] = 'N/A'

    return room_analytics


def get_peripherals(device_id: str) -> list[dict]:
    """
    Get Device Peripherals information for a specific device
    :param device_id: Unique Webex Device ID
    :return: Dictionary of enriched Peripherals information for device and dashboard display
    """
    peripheral_information = []

    # Get peripherals
    peripherals = device_api.get_peripherals(device_id)

    for peripheral in peripherals:
        peripheral_information.append({
            'name': peripheral.get('Name', ''),
            'hw_type': peripheral.get('Type', ''),
            'state': peripheral.get('Status', ''),
            'serial': peripheral.get('SerialNumber', ''),
            'hw_info': peripheral.get('HardwareInfo', ''),
            'sw_info': peripheral.get('SoftwareInfo', '')
        })

    return peripheral_information


def active_device_calls(device_ids: list[str], device_lookup: dict) -> list[dict]:
    """
    Get Active Calls for either a specific device or all devices
    :param device_ids: One or more Webex Device IDs
    :param device_lookup: Small dict, able to look up a device by ID and access additional fields
    :return: a list of active calls (dicts) to display on dashboard
    """
    # Get All Active Calls Across Devices
    current_calls = device_api.get_active_calls(device_ids)

    # Build Web Page Display Table
    device_calls = []
    for call in current_calls:
        # Get Device Details
        device = device_lookup[call['deviceId']]

        # Get Device Media Channels (use this to determine Audio and Video MOSS for Call)
        media_channels = device_api.get_call_media_channels(call['deviceId'], call['id'])

        # Determine MOSS Audio
        audio_in_jit = None
        audio_out_jit = None
        audio_in_loss = None
        audio_out_loss = None

        media_channels_audio = media_channels['Audio']
        if media_channels_audio['Incoming']:
            audio_in_jit = media_channels_audio['Incoming']['MaxJitter']
            try:
                audio_in_loss = round(((media_channels_audio['Incoming']['LastIntervalLost'] /
                                        media_channels_audio['Incoming']['LastIntervalReceived']) * 100), 2)
            except ZeroDivisionError:
                audio_in_loss = 0.0

        if media_channels_audio['Outgoing']:
            audio_out_jit = media_channels_audio['Outgoing']['MaxJitter']
            try:
                audio_out_loss = round(((media_channels_audio['Outgoing']['LastIntervalLost'] /
                                         media_channels_audio['Outgoing']['LastIntervalReceived']) * 100), 2)
            except ZeroDivisionError:
                audio_out_loss = 0.0

        audio_moss = util.calculate_mos(audio_in_jit, audio_out_jit, audio_in_loss, audio_out_loss)

        # Determine MOSS Video
        video_in_jit = None
        video_out_jit = None
        video_in_loss = None
        video_out_loss = None

        media_channels_video = media_channels['Video']
        if media_channels_video['Incoming']:
            video_in_jit = media_channels_video['Incoming']['MaxJitter']
            try:
                video_in_loss = round(((media_channels_video['Incoming']['LastIntervalLost'] /
                                    media_channels_video['Incoming']['LastIntervalReceived']) * 100), 2)
            except ZeroDivisionError:
                video_in_loss = 0.0

        if media_channels_video['Outgoing']:
            video_out_jit = media_channels_video['Outgoing']['MaxJitter']
            try:
                video_out_loss = round(((media_channels_video['Outgoing']['LastIntervalLost'] /
                                     media_channels_video['Outgoing']['LastIntervalReceived']) * 100), 2)
            except ZeroDivisionError:
                video_out_loss = 0.0

        video_moss = util.calculate_mos(video_in_jit, video_out_jit, video_in_loss, video_out_loss)

        # Convert Raw Duration (seconds) to HH:MM:SS format
        if 'Duration' in call:
            duration_string = util.convert_seconds_to_time(call['Duration'])

            # Calculate Start time based on Current DateTime (using device site timezone) - Duration
            start_time_string = util.calculate_start_time(call['Duration'], device[14])
        else:
            duration_string = 'Unknown'
            start_time_string = 'Unknown'

        device_calls.append({
            'endpoint': device[0],
            'site': device[8],
            'region': device[11],
            'id': call.get('id', '-1'),
            'displayName': call.get('DisplayName', 'Unknown'),
            'remoteNumber': call.get('RemoteNumber', 'Unknown'),
            'type': call.get('CallType', 'Unknown'),
            'direction': call.get('Direction', 'Unknown'),
            'startTime': start_time_string,
            'duration': duration_string,
            'status': call.get('Status', 'Unknown'),
            'a_mos': audio_moss,
            'v_mos': video_moss,
            'deviceType': call.get('DeviceType', 'Unknown'),
            'protocol': call.get('Protocol', 'Unknown')
        })

    return device_calls


def get_conn() -> sqlite3.Connection:
    """
    Open a new database connection if there is none yet for the current application context.
    """
    if 'conn' not in g:
        g.conn = db.create_connection(app.config['DATABASE'])
    return g.conn


@app.teardown_appcontext
def close_conn(error):
    """
    Closes the database again at the end of the request (using teardown of app context)
    """
    conn = g.pop('conn', None)
    if conn is not None:
        db.close_connection(conn)


# Routes
@app.route('/')
def index():
    """
    Main Device Summary landing page
    """
    logger.info(f"Main Index {request.method} Request:")

    # Get DB connection in request
    conn = get_conn()

    # Get all devices
    devices = db.query_all_devices(conn, "*")

    return render_template('index.html', hiddenLinks=False, devices=devices,
                           timeAndLocation=getSystemTimeAndLocation())


@app.route('/active_calls')
def active_calls():
    """
    All Active Calls page (across all devices)
    """
    logger.info(f"Active Calls {request.method} Request:")

    # Get DB connection in request
    conn = get_conn()

    # Get all devices
    devices = db.query_all_devices(conn, "*")

    # Construct quick lookup dict and device_id list
    device_lookup = {}
    device_ids = []
    for device in devices:
        key = device[0]  # device_id
        values = list(device[1:])  # everything else
        device_lookup[key] = values

        device_ids.append(device[0])

    # Get active calls across ALL devices
    device_calls = active_device_calls(device_ids, device_lookup)

    return render_template('active_calls.html', hiddenLinks=False, display_table=device_calls,
                           timeAndLocation=getSystemTimeAndLocation())


@app.route('/call_report')
def call_report():
    """
    Call History page (initially empty table, queried via AJAX on the JS side to get call history information)
    """
    logger.info(f"Call History {request.method} Request:")

    # Get DB connection in request
    conn = get_conn()

    # Get all devices
    devices = db.query_all_devices(conn, "*")

    # build Endpoint Selection structure
    endpoint_selection = []
    for device in devices:
        endpoint_selection.append({
            "deviceId": device[0],
            "displayName": device[1]
        })

    # Table initially empty, populated with query route
    return render_template('call_report.html', hiddenLinks=False, endpoint_selection=endpoint_selection,
                           display_table=[],
                           timeAndLocation=getSystemTimeAndLocation())


@app.route('/call_report/query', methods=['POST'])
def query_call_history_db():
    """
    Query Call History method (triggered via AJAX, returns calls for one or more specific devices)
    """
    logger.info(f"Query Call History DB {request.method} Request:")

    # Get parameters of query
    endpoint_id = request.form.get('endpoint')

    if endpoint_id == 'all':
        endpoint_id = None

    period_hours = request.form.get('period')

    # Get DB connection in request
    conn = get_conn()

    # Get all devices
    devices = db.query_all_devices(conn, "*")

    # Construct quick lookup dict and device_id list
    device_lookup = {}
    for device in devices:
        key = device[0]  # device_id
        values = list(device[1:])  # everything else
        device_lookup[key] = values

    results = db.query_call_history(conn, endpoint_id=endpoint_id, time_period_hours=int(period_hours))

    # Build Web Page Display Table
    display_table = []
    for call in results:
        # Get Device Details
        device = device_lookup[call[1]]

        # Convert Raw Duration (seconds) to HH:MM:SS format
        duration_string = util.convert_seconds_to_time(call[7])

        # Convert Start and End time
        start_time_utc = datetime.strptime(call[5], '%Y-%m-%d %H:%M:%S')
        end_time_utc = datetime.strptime(call[6], '%Y-%m-%d %H:%M:%S')

        # Convert UTC datetime objects to your local timezone
        if device[14] != 'N/A':
            local_timezone = pytz.timezone(device[14])
        else:
            local_timezone = pytz.utc

        start_time_local = start_time_utc.replace(tzinfo=pytz.utc).astimezone(local_timezone)
        end_time_local = end_time_utc.replace(tzinfo=pytz.utc).astimezone(local_timezone)

        # Format the local datetime objects into the desired format
        start_time_string = start_time_local.strftime('%m/%d/%y %I:%M:%S %p (%Z)')
        end_time_string = end_time_local.strftime('%m/%d/%y %I:%M:%S %p (%Z)')

        display_table.append({
            'endpoint': device[0],
            'region': device[11],
            'site': device[8],
            'ipAddr': device[4],
            'displayName': call[2],
            'callbackNumber': call[3],
            'remoteNumber': call[4],
            'startTime': start_time_string,
            'endTime': end_time_string,
            'duration': duration_string,
            'disconnect_reason': call[8],
            'a_moss': call[9],
            'v_moss': call[10],
            'a_pkt_loss_max': call[11],
            'v_pkt_loss_max': call[12],
            'a_jit_max': call[13],
            'v_jit_max': call[14]
        })

    return display_table


@app.route('/device/details')
def get_device_details():
    """
    Device Details page, obtains various Device Details like: System Unit Info, Active Calls, Peripherals, etc.
    """
    # Get Device ID from URL params
    deviceId = request.args.get('deviceId')

    # Get Specific Device from Device List (get most recent details from API, updates DB entry)
    device = lookup_device_details(device_api, deviceId)

    logger.info(f"Device Detail {request.method} Request for {device['displayName']}:")

    # Get Region value
    conn = get_conn()
    device_region = db.query_device(conn, deviceId, "region")[0][0]

    # Get Existing Regions
    existing_regions = []
    device_regions = db.query_all_devices(conn, "region")
    for region in device_regions:
        if region[0] != 'None':
            existing_regions.append(region[0])

    # Get all devices
    lookup_devices = db.query_all_devices(conn, "*")

    # Construct quick lookup dict and device_id list
    device_lookup = {}
    for lookup_device in lookup_devices:
        key = lookup_device[0]  # device_id
        values = list(lookup_device[1:])  # everything else
        device_lookup[key] = values

    # Get Device Details (A Series of XAPI Calls)
    device_details = {
        "deviceId": deviceId,
        "displayName": device.get('displayName', ''),
        "connectionStatus": device.get('connectionStatus', ''),
        "contactInformation": device.get('room', ''),
        "region": device_region,
        "localNumber": device.get('primarySipUrl', ''),
        "systemUnit": get_system_unit_information(deviceId, device),
        "roomAnalytics": get_room_analytics(deviceId),
        "activeCalls": active_device_calls([deviceId], device_lookup),
        "peripherals": get_peripherals(deviceId)
    }

    return render_template('device_details.html', hiddenLinks=False, device_details=device_details,
                           existing_regions=existing_regions, timeAndLocation=getSystemTimeAndLocation())


@app.route('/open_incidents')
def open_incidents():
    """
    Open ServiceNow Incidents page (incidents pulled based on filters in config.py)
    """
    logger.info(f"Open Incidents {request.method} Request:")

    # Check if ServiceNow Functionality enabled (else redirect to index)
    if config.SERVICE_NOW_FEATURE:
        # Get List ServiceNow Incidents
        incidents = snow.get_service_now_incidents(config.OPEN_SERVICE_NOW_INCIDENT_FILTER)

        display_table = []
        for incident in incidents:
            # Get Endpoint Name (if enabled - note design assumptions in README)
            if config.INCLUDE_ENDPOINT_NAME:
                endpoint_name = incident['short_description']
            else:
                endpoint_name = ""

            # Convert Open Time to Proper UTC Format
            open_time_datetime = datetime.strptime(incident['opened_at'], "%Y-%m-%d %H:%M:%S")
            formatted_open_time = open_time_datetime.strftime('%m/%d/%y %I:%M:%S %p (UTC)')

            display_table.append({
                "incident_num": incident['number'],
                "opened_at": formatted_open_time,
                "status": incident["state"],
                "endpoint": endpoint_name,
                "urgency": incident["urgency"],
                "description": incident["description"]
            })

        return render_template('open_incidents.html', hiddenLinks=False, display_table=display_table,
                               timeAndLocation=getSystemTimeAndLocation())
    else:
        return redirect(url_for("index"))


@app.route('/closed_incidents')
def closed_incidents():
    """
    Closed ServiceNow Incidents page (incidents pulled based on filters in config.py)
    """
    logger.info(f"Closed Incidents {request.method} Request:")

    # Check if ServiceNow Functionality enabled (else redirect to index)
    if config.SERVICE_NOW_FEATURE:
        # Calculate timestamp within last "last_hours" ago
        time_X_hours_ago = datetime.now() - timedelta(hours=config.LAST_X_HOURS)

        # Format the date and time for gs.dateGenerate
        date_str = time_X_hours_ago.strftime('%Y-%m-%d')
        time_str = time_X_hours_ago.strftime('%H:%M:%S')

        closed_filter = copy.deepcopy(config.CLOSED_SERVICE_NOW_INCIDENT_FILTER)
        closed_filter['sysparm_query'] += f"^closed_at>=javascript:gs.dateGenerate('{date_str}', '{time_str}')"

        # Get List ServiceNow Incidents
        incidents = snow.get_service_now_incidents(closed_filter)

        display_table = []
        for incident in incidents:
            # Get Endpoint Name (if enabled - note design assumptions in README)
            if config.INCLUDE_ENDPOINT_NAME:
                endpoint_name = incident['short_description']
            else:
                endpoint_name = ""

            # Convert Open/Close Time to Proper UTC Format
            open_time_datetime = datetime.strptime(incident['opened_at'], "%Y-%m-%d %H:%M:%S")
            formatted_open_time = open_time_datetime.strftime('%m/%d/%y %I:%M:%S %p (UTC)')

            close_time_datetime = datetime.strptime(incident['closed_at'], "%Y-%m-%d %H:%M:%S")
            formatted_close_time = close_time_datetime.strftime('%m/%d/%y %I:%M:%S %p (UTC)')

            display_table.append({
                "incident_num": incident['number'],
                "opened_at": formatted_open_time,
                "closed_at": formatted_close_time,
                "status": incident["state"],
                "endpoint": endpoint_name,
                "severity": incident["severity"],
                "description": incident["description"]
            })

        return render_template('closed_incidents.html', hiddenLinks=False, display_table=display_table,
                               hours=config.LAST_X_HOURS, timeAndLocation=getSystemTimeAndLocation())
    else:
        return redirect(url_for("index"))


@app.route('/download/excel', methods=['POST'])
def download_excel():
    """
    Download Excel method, triggered from button press on various webpages. Downloads provided table data to Excel file
    """
    # Obtain type of download request (active calls, historic calls)
    download_type = request.form.get('download_type')

    logger.info(f"Excel Download {request.method} Request ({download_type}):")

    # Obtain Table Data for Excel File
    data = request.form.get('data')
    data = json.loads(data)

    # Convert data to DataFrame
    df = pd.DataFrame(data)

    # Create in-memory file for writing Excel data
    output = BytesIO()

    # Write DataFrame to Excel file
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Active Calls')

    # Set filename with current date and time
    current_datetime = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    if download_type == "active":
        filename = f"active_calls_{current_datetime}.xlsx"
    elif download_type == "device":
        filename = f"device_list_{current_datetime}.xlsx"
    else:
        filename = f"call_history_{current_datetime}.xlsx"

    logger.info(f"Download complete!")

    # Return Excel file as a response to the request
    response = Response(output.getvalue(), mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response.headers.set('Content-Disposition', 'attachment', filename=filename)
    return response


@app.route('/update_region', methods=['POST'])
def update_region():
    """
    Update device region, triggered from text box or selecting an existing entry in the dropdown on a Device Details page
    """
    logger.info(f"Update Region {request.method} Request:")

    # Retrieve deviceId
    deviceId = request.form['deviceId']

    # Retrieve either new region or selected existing region
    new_region = request.form['newRegion']
    existing_region = request.form['existingRegions']

    # Update the region in the database based on the form data
    if new_region != '':
        updated_region = new_region
    elif existing_region:
        updated_region = existing_region
    else:
        # Handle case where neither new region nor existing region is provided
        return jsonify({'error': 'No region provided'}), 400

    # Update Region
    conn = get_conn()
    db.update_device_region(conn, deviceId, updated_region)

    # Return updated region to update UI
    return jsonify({'new_region': updated_region}), 200


# One Time Actions (schedule call history and device list background thread - every X minutes - trigger devices now,
# call history in 5 minutes)
job = scheduler.add_job(get_devices_periodically, args=[device_api_background], trigger='interval', minutes=5)
job.modify(next_run_time=datetime.now())

job = scheduler.add_job(get_device_call_history_periodically, args=[device_api_background], trigger='interval',
                        minutes=config.CALL_HISTORY_REFRESH_CYCLE)
delayed_start = datetime.now() + timedelta(minutes=5)
job.modify(next_run_time=delayed_start)

scheduler.start()

if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=False)
