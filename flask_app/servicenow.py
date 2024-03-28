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

import logging
import os

import requests
from dotenv import load_dotenv

# Load in Environment Variables
load_dotenv()
SERVICENOW_INSTANCE = os.getenv('SERVICENOW_INSTANCE')
SERVICENOW_USERNAME = os.getenv('SERVICENOW_USERNAME')
SERVICENOW_PASSWORD = os.getenv('SERVICENOW_PASSWORD')


class ServiceNow:
    """
    ServiceNow API Class, includes various methods for interacting with ServiceNow REST API
    """

    def __init__(self, logger: logging.Logger):
        """
        Initialize the ServiceNow class
        """
        self.headers = {"Content-Type": "application/json", "Accept": "application/json"}
        self.auth = (SERVICENOW_USERNAME, SERVICENOW_PASSWORD)
        self.logger = logger

    def get_service_now_incidents(self, params: dict) -> list[dict]:
        """
        Get ServiceNow Tickets using filter query
        """
        # Create new ServiceNow Ticket using ticket_data
        response = requests.get(SERVICENOW_INSTANCE + "/api/now/table/incident", auth=self.auth, headers=self.headers,
                                params=params)

        if response.ok:
            incidents = response.json()['result']

            self.logger.info(f'{len(incidents)} total ServiceNow Incidents Retrieved')
            return incidents
        else:
            self.logger.error(f'Failed to retrieve Service Now incidents: {response.text}')
            return []
