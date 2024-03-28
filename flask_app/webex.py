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

import json
import logging
import os
import sys
import time

import requests
from dotenv import load_dotenv
from requests_oauthlib import OAuth2Session
from rich.console import Console

# Load env variables
load_dotenv()
WEBEX_CLIENT_ID = os.getenv("WEBEX_CLIENT_ID")
WEBEX_CLIENT_SECRET = os.getenv("WEBEX_CLIENT_SECRET")

# Global Variables
TOKEN_URL = 'https://api.ciscospark.com/v1/access_token'
BASE_URL = 'https://webexapis.com/v1/'
XAPI_STATUS_URL = "xapi/status"
XAPI_COMMAND_URL = "xapi/command"

# Absolute Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
tokens_path = os.path.join(script_dir, 'tokens.json')


def refresh_token(tokens: dict, logger: logging.Logger) -> dict:
    """
    Refresh Webex token if primary token is expired (assumes refresh token is valid)
    :param tokens: Primary and Refresh Tokens
    :param logger: Logger Object
    :return: New set of tokens
    """
    refresh_token = tokens['refresh_token']
    extra = {
        'client_id': WEBEX_CLIENT_ID,
        'client_secret': WEBEX_CLIENT_SECRET,
        'refresh_token': refresh_token,
    }
    auth_code = OAuth2Session(WEBEX_CLIENT_ID, token=tokens)
    new_teams_token = auth_code.refresh_token(TOKEN_URL, **extra)

    # store away the new token
    with open(tokens_path, 'w') as json_file:
        json.dump(new_teams_token, json_file)

    logger.info("A new token has been generated and stored in `tokens.json`")
    return new_teams_token


def get_webex_token(logger: logging.Logger) -> str:
    """
    Get Valid Webex Access Token (from OAuth token workflow, Refresh Workflow, or end application if both tokens expired)
    :param logger: Logger Object
    :return: Valid Webex API Access Token
    """
    # If token file already exists, extract existing tokens
    if os.path.exists(tokens_path):
        with open(tokens_path) as f:
            tokens = json.load(f)
    else:
        tokens = None

    # Determine relevant route to obtain a valid Webex Token. Options include:
    # 1. Full OAuth (both primary and refresh token are expired)
    # 2. Simple refresh (only the primary token is expired)
    # 3. Valid token (the existing primary token is valid)
    if tokens is None or time.time() > (
            tokens['expires_at'] + (tokens['refresh_token_expires_in'] - tokens['expires_in'])):
        # Both tokens expired, run the OAuth Workflow
        logger.error("Both tokens are expired, we need to run OAuth workflow... See README.")
        sys.exit(0)
    elif time.time() > tokens['expires_at']:
        # Generate a new token using the refresh token
        logger.info("Existing primary token expired! Using refresh token...")
        tokens = refresh_token(tokens, logger)
    else:
        # Use existing valid token
        logger.info("Existing primary token is valid!")

    return tokens['access_token']


def get_next_page_url(link_header: str):
    """
    Get the next page URL (if pagination present in response within get_wrapper), return next page URL
    :param link_header: Original response headers (contains 'link' element with next page URL)
    :return: Next page URL
    """
    if link_header:
        # Extract get link within links header
        links = link_header.split(',')
        for link in links:
            link_info = link.split(';')
            # Return raw link, ignore special characters and other surrounding characters
            if len(link_info) == 2 and 'rel="next"' in link_info[1]:
                return link_info[0].strip('<> ')
    return None


class WebexDeviceAPI:
    """
    Webex Devices API Class, includes various methods for interacting with Webex Device APIs (including xAPI)
    """

    def __init__(self, token: str, logger: logging.Logger):
        self.headers = {'Authorization': f'Bearer {token}'}
        self.logger = logger if logger else Console()

    def get_wrapper(self, url: str, params: dict, headers=None) -> dict | None:
        """
        REST Get API Wrapper, includes support for paging, 429 rate limiting, and error handling
        :param headers: Optional Headers (used to execute calls with Webex Bot Token)
        :param url: Resource URL
        :param params: REST API Query Params
        :return: Response Payload (aggregated if multiple pages present)
        """
        # Build Get Request Components
        results = {}
        next_url = f'{BASE_URL}{url}'
        retry_count = 0

        while next_url:
            response = requests.get(url=next_url, headers=headers if headers else self.headers, params=params)

            if response.ok:
                response_data = response.json()

                # Combine like fields across multiple pages, create an aggregated structure
                for val in response_data:
                    if val in results:
                        results[val].extend(response_data[val])
                    else:
                        results[val] = response_data[val]

                # Check if there is a next page
                next_url = get_next_page_url(response.headers.get('link'))

                # Clear params to avoid 4XX errors
                if next_url:
                    params = {}
            elif response.status_code == 429:
                # Handle 429 Too Many Requests error (25 maximum retries to avoid infinite loops)
                if retry_count < 25:
                    retry_count += 1
                    sleep_time = int(
                        response.headers.get('Retry-After', 10))  # Default to 10 seconds if Retry-After is not provided
                    time.sleep(sleep_time)
                else:
                    self.logger.info("Rate limit exceeded, maximum amount of retries exceeded.")
                    return None
            else:
                # Print failure message on error
                self.logger.error("Request FAILED: " + str(
                    response.status_code) + '\n' + f'Response Headers: {response.headers}' + '\n' + f'Response Params: {params}' + '\n' + f'Response Content: {response.text}')
                return None

        return results

    def post_wrapper(self, url: str, params: dict, body: dict, headers=None) -> dict | None:
        """
        REST POST API Wrapper, includes support for 429 rate limiting and error handling
        :param headers: Optional Headers (used to execute calls with Webex Bot Token)
        :param url: Resource URL
        :param params: REST API Query Params
        :param body: REST API Body
        :return: Response Payload
        """
        # Build Get Request Components
        target_url = f'{BASE_URL}{url}'
        retry_count = 0

        response = requests.post(url=target_url, headers=headers if headers else self.headers, params=params, json=body)

        while retry_count < 25:
            if response.ok:
                response_data = response.json()
                return response_data
            elif response.status_code == 429:
                # Handle 429 Too Many Requests error (25 maximum retries to avoid infinite loops)
                retry_count += 1
                sleep_time = int(
                    response.headers.get('Retry-After', 10))  # Default to 10 seconds if Retry-After is not provided
                time.sleep(sleep_time)
            else:
                # Print failure message on error
                self.logger.error("Request FAILED: " + str(
                    response.status_code) + '\n' + f'Response Headers: {response.headers}' + '\n' + f'Response Params: {params}' + '\n' + f'Response Content: {response.text}')
                return None

        self.logger.info("Rate limit exceeded, maximum amount of retries exceeded.")
        return None

    #### Webex API Methods ####
    def get_all_devices(self, device_type: str) -> list[dict]:
        """
        Get All Devices with XAPI permissions and optional type (critical to have these permissions, otherwise most data is unreachable)
        :param device_type: Device type to filter on (specified in config.py - roomdesk, phone, accessory, webexgo, unknown)
        :return: List of all Webex Devices (of specific type and XAPI permissions)
        """
        devices = []

        # Get Device ID's for devices which the user has XAPI permissions to
        devices_url = f"devices"
        params = {'permission': 'xapi'}
        if device_type != "":
            params['type'] = device_type

        response = self.get_wrapper(devices_url, params)

        if response and 'items' in response:
            found_devices = []
            for device in response['items']:
                # Add Device ID to the list
                devices.append(device)

                found_devices.append(device['displayName'])

            self.logger.info(f"Found the following XAPI Enabled Devices: {found_devices}")
        else:
            self.logger.error("No eligible XAPI devices found...")

        return devices

    def get_device_details(self, device_id: str) -> dict:
        """
        Get Webex Device Details for specific device (name, ip, etc.)
        :param device_id: Unique Device ID
        :return: Device details
        """
        # Get Device Details
        device_details_url = f"devices/{device_id}"
        params = {}

        response = self.get_wrapper(device_details_url, params)
        self.logger.info(f"Found the following Device Details for device_id ({device_id}): {response}")

        return response

    def get_workspace_details(self, workspace_id: str) -> dict:
        """
        Get Device Workspace details (assigned room - if assigned)
        :param workspace_id: Unique workspace ID
        :return: Workspace details
        """
        # Get Workspace Details
        workspace_details_url = f"workspaces/{workspace_id}"
        params = {}

        response = self.get_wrapper(workspace_details_url, params)
        self.logger.info(f"Found the following Workspace Details for workspace_id ({workspace_id}): {response}")

        return response

    def get_location_details(self, location_id: str) -> dict:
        """
        Get Device Location details (assigned Site in Control Hub)
        :param location_id: Unique Location ID
        :return: Location details
        """
        # Get Location Details
        location_details_url = f"locations/{location_id}"
        params = {}

        response = self.get_wrapper(location_details_url, params)
        self.logger.info(f"Found the following Location Details for location_id ({location_id}): {response}")

        return response

    #### XAPI Methods ####
    def get_active_calls(self, device_ids: list[str]) -> list[dict]:
        """
        Get All active calls across provided device_ids list (xAPI call - current call queue)
        :param device_ids: List of Webex Device ids
        :return: List of Active calls across device_ids (includes information like remote number, call name, etc.)
        """
        active_calls = []

        # Get Active Call Status on all devices
        for device_id in device_ids:
            response = self.get_wrapper(XAPI_STATUS_URL, {'name': 'Call[*].*', 'deviceId': device_id})

            if response and 'Call' in response['result']:
                # Add Device ID field to tie each call to a device
                calls_with_deviceid = [{**d, 'deviceId': device_id} for d in response['result']['Call']]

                # Append All Device Calls to active_calls
                active_calls += calls_with_deviceid

        self.logger.info(f"Found the following active calls for device_ids ({device_ids}): {active_calls}")
        return active_calls

    def get_call_history(self, device_ids: list[str]) -> dict:
        """
        Get Call History for historic calls made on all provided device_ids (xAPI call - historic call queue)
        :param device_ids: List of Webex Device ids
        :return: Dictionary mapping a device's unique id to a list of historic calls made on the device (last 30 days - API max)
        """
        call_history = {}

        # Get Call History on all devices
        for device_id in device_ids:
            response = self.post_wrapper(f"{XAPI_COMMAND_URL}/CallHistory.Get", {},
                                         {'deviceId': device_id, 'arguments': {"DetailLevel": "Full"}})

            if response and 'Entry' in response['result']:
                # Add Device ID field to tie each call to a device
                calls_with_deviceid = [{**d, 'deviceId': device_id} for d in response['result']['Entry']]

                # Create new entry is call history dictionary mapped to device_id
                call_history[device_id] = calls_with_deviceid

        self.logger.info(f"Found the following call history for device_ids ({device_ids}): {call_history}")
        return call_history

    def get_call_media_channels(self, device_id: str, call_id: str) -> dict:
        """
        Get all relevant Media Channels associated with an active call on a Webex Device (separate channels for audio and video incoming and outgoing).
        These channels identify which channel is which when examining device Netstat information to determine MOS scores for active calls
        :param device_id: Unique Webex Device id
        :param call_id: Unique Call id
        :return: Dictionary mapping Audio and Video Incoming and Outgoing Netstat information for their respective channels
        """
        relevant_media_channels = {'Audio': {'Incoming': None, 'Outgoing': None},
                                   'Video': {'Incoming': None, 'Outgoing': None}}

        response = self.get_wrapper(XAPI_STATUS_URL,
                                    {'name': f'MediaChannels.Call[{call_id}].Channel[*].*', 'deviceId': device_id})
        if response and 'MediaChannels' in response['result']:
            call = response['result']['MediaChannels']['Call'][0]

            # Process all channels for call
            for channel in call['Channel']:
                # Only Care about Channels with Net Stat
                if 'Netstat' in channel:
                    # Audio (Main)
                    if channel['Type'] == 'Audio' and channel['Audio']['ChannelRole'] == 'Main':
                        if channel['Direction'] == 'Incoming':
                            # Incoming
                            relevant_media_channels['Audio']['Incoming'] = channel['Netstat']
                        elif channel['Direction'] == 'Outgoing':
                            # Outgoing
                            relevant_media_channels['Audio']['Outgoing'] = channel['Netstat']
                    # Video (Main)
                    elif channel['Type'] == 'Video' and channel['Video']['ChannelRole'] == 'Main':
                        if channel['Direction'] == 'Incoming':
                            # Incoming
                            relevant_media_channels['Video']['Incoming'] = channel['Netstat']
                        elif channel['Direction'] == 'Outgoing':
                            # Outgoing
                            relevant_media_channels['Video']['Outgoing'] = channel['Netstat']

        self.logger.info(
            f"Found the following media channels for device_id ({device_id}) and call id ({call_id}): {relevant_media_channels}")
        return relevant_media_channels

    def get_system_unit_information(self, device_id: str) -> dict:
        """
        Get Device System Unit information (xAPI call - retrieves OS, MAC, IP, etc.)
        :param device_id: Unique Webex Device id
        :return: System Unit Information
        """
        # Get System unit configuration on device
        response = self.get_wrapper(XAPI_STATUS_URL, {'name': 'SystemUnit.*', 'deviceId': device_id})

        if response and 'SystemUnit' in response['result']:
            system_unit_info = response['result']['SystemUnit']

            self.logger.info(
                f"Found the following system unit information for device_id ({device_id}): {system_unit_info}")
            return system_unit_info
        else:
            self.logger.error(f"Unable to find system unit information for device_id ({device_id}): {response}")
            return {}

    def get_room_analytics(self, device_id: str) -> dict:
        """
        Get Device Room Analytics information (xAPI call - ultrasound, head tracking detection of people in the room)
        :param device_id: Unique Webex Device ID
        :return: Room Analytics Information
        """
        # Get room analytics information from device
        response = self.get_wrapper(XAPI_STATUS_URL, {'name': 'RoomAnalytics.*', 'deviceId': device_id})

        if response and 'RoomAnalytics' in response['result']:
            room_analytics_info = response['result']['RoomAnalytics']

            self.logger.info(
                f"Found the following room analytics information for device_id ({device_id}): {room_analytics_info}")
            return room_analytics_info
        else:
            self.logger.error(f"Unable to find room analytics information for device_id ({device_id}): {response}")
            return {}

    def get_audio_information(self, device_id: str) -> dict:
        """
        Get Webex Device Audio Settings information (xAPI call - Mute, Volume, etc.)
        :param device_id: Unique Webex Device id
        :return: Webex Device Audio Information
        """
        # Get audio configuration for device
        response = self.get_wrapper(XAPI_STATUS_URL, {'name': 'Audio.*', 'deviceId': device_id})

        if response and 'Audio' in response['result']:
            audio_information = response['result']['Audio']

            self.logger.info(f"Found the following audio information for device_id ({device_id}): {audio_information}")
            return audio_information
        else:
            self.logger.error(f"Unable to find audio information for device_id ({device_id}): {response}")
            return {}

    def get_peripherals(self, device_id: str) -> dict:
        """
        Get Webex Device Peripherals information (connected peripherals, etc.)
        :param device_id: Unique Webex Device id
        :return: Webex Device Peripherals information
        """
        # Get audio configuration for device
        response = self.get_wrapper(XAPI_STATUS_URL,
                                    {'name': 'Peripherals.ConnectedDevice[*].*', 'deviceId': device_id})

        if response and 'Peripherals' in response['result']:
            peripheral_information = response['result']['Peripherals']['ConnectedDevice']

            self.logger.info(
                f"Found the following peripheral information for device_id ({device_id}): {peripheral_information}")
            return peripheral_information
        else:
            self.logger.error(f"Unable to find peripheral information for device_id ({device_id}): {response}")
            return {}
