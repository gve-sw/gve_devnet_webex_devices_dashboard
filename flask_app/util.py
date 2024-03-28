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
from datetime import datetime, timedelta
from logging.handlers import TimedRotatingFileHandler

import pytz
import rich.logging

# Absolute Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
logs_path = os.path.join(script_dir, 'logs')


def set_up_logging() -> logging.Logger:
    """
    Return Main Dashboard Logger Object (Rich Logger for Console Output, TimeRotatingFileHandler for Log Files)
    :return: Logger Object
    """
    # Set up logging
    logger = logging.getLogger('my_logger')
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(funcName)s:%(lineno)d - %(message)s')

    # log to stdout
    stream_handler = rich.logging.RichHandler()
    stream_handler.setLevel(logging.INFO)

    # log to files (last 7 days, rotated at midnight local time each day)
    log_file = os.path.join(logs_path, 'dashboard_logs.log')
    file_handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=7)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

    return logger


def set_up_logging_background() -> logging.Logger:
    """
    Return Background Task Logger Object (TimeRotatingFileHandler for Log Files)
    :return: Logger Object
    """
    # Set up logging
    logger = logging.getLogger('my_logger_background')
    logger.setLevel(logging.INFO)

    formatter = logging.Formatter('%(asctime)s %(levelname)s: %(funcName)s:%(lineno)d - %(message)s')

    # log to files (last 7 days, rotated at midnight local time each day)
    log_file = os.path.join(logs_path, 'call_history_task.log')
    file_handler = TimedRotatingFileHandler(log_file, when="midnight", interval=1, backupCount=7)
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(file_handler)

    return logger


def convert_seconds_to_time(seconds: int) -> str:
    """
    Convert seconds to formatted string of HH MM SS (used primarily for 'Duration' conversations)
    :param seconds: Seconds value
    :return: Formatted string (easier to read)
    """
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    seconds = seconds % 60
    return '{:02d}H: {:02d}M: {:02d}S'.format(hours, minutes, seconds)


def calculate_start_time(duration_seconds: int, timezone: datetime.tzinfo):
    """
    Given a duration, calculate the start time of an event from the current time stamp and in the correct timezone
    :param duration_seconds: How many seconds since an event started
    :param timezone: Event timezone (typically determined by associated device's location timezone)
    :return: Formatted string showing the starting datetime of the event
    """
    # Define the time zone (if provided, else, UTC)
    if timezone != 'N/A':
        zone = pytz.timezone(timezone)
    else:
        zone = pytz.utc

    # Get current date and time
    current_time = datetime.now(zone)

    # Calculate start time by subtracting duration from current time
    start_time = current_time - timedelta(seconds=duration_seconds)

    # Format start time in the desired format
    formatted_start_time = start_time.strftime('%m/%d/%y %I:%M:%S %p (%Z)')

    return formatted_start_time


def calculate_mos(incoming_jitter: float | None, outgoing_jitter: float | None,
                  incoming_packet_loss_percent: int | None, outgoing_packet_loss_percent: int | None) -> float | str:
    """
    Calculate MOSS scores for Audio and Video based on jitter and packet loss, choose the minimum of the scores between incoming and outgoing calculations
    :param incoming_jitter: Incoming stream jitter
    :param outgoing_jitter: Outgoing stream jitter
    :param incoming_packet_loss_percent: Incoming stream packet loss
    :param outgoing_packet_loss_percent: Outgoing stream packet loss
    :return: Minimum of 2 calculated stream MOSS values
    """
    # Initialize a list to hold the scores
    scores = []

    # Define the calculation logic as a nested function for reuse
    def calculate_score(jitter: float, packet_loss_percent: int) -> float:
        """
        Perform actual MOSS calculations (see README for explanation of equation)
        :param jitter: Jitter value (ms - float)
        :param packet_loss_percent: Packet loss percent value (int - %)
        :return: Calculated MOSS score
        """
        # Base score
        base_score = 5.00

        # Thresholds and penalties for jitter
        jitter_threshold = 10.00  # in ms
        jitter_penalty_per_10ms_over_threshold = 0.1

        # Thresholds and penalties for packet loss
        packet_loss_good_threshold = 2.0  # Up to 2% is considered good
        packet_loss_fair_threshold = 5.0  # Between 2% and 5% is fair to poor
        packet_loss_penalty_good_to_fair = 0.5  # Penalty for exceeding good threshold but less than fair
        packet_loss_penalty_fair_to_poor = 1.0  # Additional penalty for exceeding fair threshold

        # Calculate penalties
        jitter_penalty = max(0.0, (jitter - jitter_threshold) / 10.00) * jitter_penalty_per_10ms_over_threshold
        if packet_loss_percent <= packet_loss_good_threshold:
            packet_loss_penalty = 0.0
        elif packet_loss_percent <= packet_loss_fair_threshold:
            packet_loss_penalty = packet_loss_penalty_good_to_fair
        else:
            packet_loss_penalty = packet_loss_penalty_good_to_fair + packet_loss_penalty_fair_to_poor

        # Calculate and return the score
        return max(1.0, base_score - jitter_penalty - packet_loss_penalty)

    # Calculate incoming score if data is present
    if incoming_jitter is not None and incoming_packet_loss_percent is not None:
        scores.append(calculate_score(incoming_jitter, incoming_packet_loss_percent))

    # Calculate outgoing score if data is present
    if outgoing_jitter is not None and outgoing_packet_loss_percent is not None:
        scores.append(calculate_score(outgoing_jitter, outgoing_packet_loss_percent))

    # Return the minimum score if both scores are present, otherwise return the single score available
    return min(scores) if scores else 'N/A'
