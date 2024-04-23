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

import hashlib
import os
import sqlite3
from datetime import datetime, timedelta
from pprint import pprint
from sqlite3 import Error

import pytz

import util

# Absolute Paths
script_dir = os.path.dirname(os.path.abspath(__file__))
db_path = os.path.join(script_dir, 'db/sqlite.db')


def generate_unique_hash(device_id: str, callback_number: str, start_time: str, end_time: str) -> str:
    """
    Generate Unique Hash based on function params for each historical call (near guarantees each call will have a unique id)
    :param device_id: Device ID
    :param callback_number: Callback Number for call
    :param start_time: Start time of the call
    :param end_time: End time of the call
    :return: Unique hash value
    """
    # Concatenate the values into a single string
    concatenated_string = f"{device_id}_{callback_number}_{start_time}_{end_time}"

    # Generate the hash value using SHA-256 algorithm
    unique_hash = hashlib.sha256(concatenated_string.encode()).hexdigest()

    return unique_hash


def create_connection(db_file: str) -> sqlite3.Connection:
    """
    Connect to DB
    :param db_file: DB Object
    :return: DB connection object
    """
    conn = None
    try:
        conn = sqlite3.connect(db_file)
    except Error as e:
        print(e)

    return conn


def create_tables(conn: sqlite3.Connection):
    """
    Create initial tables (device_ids, mapping one to many to call_history table)
    :param conn: DB connection object
    """
    c = conn.cursor()

    # Remove Existing Data (call history)
    c.execute("DROP TABLE IF EXISTS call_history")

    c.execute("""
              CREATE TABLE IF NOT EXISTS devices
              ([device_id] TEXT PRIMARY KEY,
               [endpoint] TEXT,
               [connection_status] TEXT,
               [product] TEXT,
               [serial] TEXT,
               [ip_addr] TEXT,
               [mac] TEXT,
               [software] TEXT,
               [mode] TEXT,
               [site] TEXT,
               [room] TEXT,
               [local_number] TEXT,
               [region] TEXT,
               [uptime] TEXT,
               [email] TEXT,
               [timezone] TEXT)
              """)

    c.execute("""
              CREATE TABLE IF NOT EXISTS call_history
              ([call_id] TEXT PRIMARY KEY, 
               [device_id] TEXT,
               [display_name] TEXT,
               [callback_number] TEXT,
               [remote_number] TEXT,
               [start_time] TEXT,
               [end_time] TEXT,
               [duration] INTEGER,
               [disconnect_reason] TEXT,
               [a_mos] REAL,
               [v_mos] REAL,
               [a_pkt_loss_max] TEXT,
               [v_pkt_loss_max] TEXT,
               [a_jit_max] TEXT,
               [v_jit_max] TEXT,
               FOREIGN KEY (device_id) REFERENCES devices (device_id))
              """)

    # Create index for device_id column
    c.execute("CREATE INDEX IF NOT EXISTS idx_device_id ON call_history(device_id)")

    # Create index for start_time column
    c.execute("CREATE INDEX IF NOT EXISTS idx_start_time ON call_history(start_time)")

    conn.commit()


def query_all_devices(conn: sqlite3.Connection, column: str) -> list[tuple[int, str]]:
    """
    Return table contents for Devices table
    :param conn: DB connection object
    :param column: Optional Column to grab specific data (or *)
    :return: All entries in devices table
    """
    c = conn.cursor()

    c.execute(f"""SELECT {column} FROM devices""")
    devices = c.fetchall()

    return devices


def query_device(conn: sqlite3.Connection, device_id: str, column: str) -> list[tuple[int, str]]:
    """
    Return specific device
    :param conn: DB connection object
    :param device_id: Specific device id to query
    :param column: Optional Column to grab specific data (or *)
    :return: Specific device in device table
    """
    c = conn.cursor()

    c.execute(f"""SELECT {column} FROM devices WHERE device_id = ?""", (device_id,))
    device = c.fetchall()

    return device


def query_all_call_history(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """
    Return table contents for Call History table
    :param conn: DB connection object
    :return: All entries in call history table
    """
    c = conn.cursor()

    c.execute("""SELECT * FROM call_history""")
    call_history = c.fetchall()

    return call_history


def query_call_history(conn: sqlite3.Connection, endpoint_id=None, time_period_hours=1) -> list[tuple[int, str]]:
    """
    Return a subset of call history entries based on endpoint (default all) and/or time period (default: 60 minutes)
    :param conn: DB connection object
    :param endpoint_id: A specific endpoint to select call history entries for (default: all endpoint - None)
    :param time_period_hours: time period to select call history entries from (default: last 1 hour)
    :return: All call history entries for a specific device (or all)
    """
    c = conn.cursor()

    # Calculate the start time based on the current time and the specified time period
    x_hours_ago_datetime = datetime.now(pytz.utc) - timedelta(hours=time_period_hours)

    # Construct the SQL query based on the selected endpoint ID and time period
    if endpoint_id:
        query = """
            SELECT *
            FROM call_history
            WHERE device_id = ? AND start_time >= ?
            ORDER BY start_time DESC
        """
        c.execute(query, (endpoint_id, x_hours_ago_datetime.strftime('%Y-%m-%d %H:%M:%S')))
    else:
        query = """
                SELECT *
                FROM call_history
                WHERE start_time >= ?
                ORDER BY start_time DESC
            """
        c.execute(query, (x_hours_ago_datetime.strftime('%Y-%m-%d %H:%M:%S'),))

    # Fetch all rows from the query result
    rows = c.fetchall()

    return rows


def add_device_entries(conn: sqlite3.Connection, device: dict):
    """
    Add (or update) device entries
    :param conn: DB connection object
    :param device: Device information dictionary
    """
    c = conn.cursor()

    # Update device if already in table (skip custom fields)
    update_statement = (f"UPDATE devices SET endpoint=?, connection_status=?, product=?, serial=?, "
                        f"ip_addr=?, mac=?, software=?, mode=?, site=?, room=?, local_number=?, uptime=?, email=?, timezone=?"
                        f"WHERE device_id=?")
    c.execute(update_statement, (
        device['displayName'], device['connectionStatus'], device['product'], device['serial'],
        device['ip'], device['mac'], device['software'], device['mode'], device['site'], device['room'],
        device['primarySipUrl'], device['uptime'], device['email'], device['timeZone'], device['id'],))

    # Add to device table if device not in device table (set custom fields to starting defaults)
    insert_statement = (f"INSERT OR IGNORE into devices (device_id, endpoint, connection_status, product, serial, "
                        f"ip_addr, mac, software, mode, site, room, local_number, region, uptime, email, timezone) VALUES (?,?,?,?,?,?,?,?,?,?,"
                        f"?,?,?,?,?,?)")
    c.execute(insert_statement, (
        device['id'], device['displayName'], device['connectionStatus'], device['product'], device['serial'],
        device['ip'],
        device['mac'], device['software'], device['mode'], device['site'], device['room'], device['primarySipUrl'],
        "None", device['uptime'], device['email'], device['timeZone']))

    conn.commit()


def add_history_entries(conn: sqlite3.Connection, x_days_ago: datetime, device_call_history: list[dict]):
    """
    Add call_history entries
    :param conn: DB connection object
    :param x_days_ago: X days ago UTC time stamp, prevents storing data > X days
    :param device_call_history: Call history list for specific device with relevant call history entry fields
    """
    c = conn.cursor()

    # Add All Call Entries for device if not present in call_history table
    for call in device_call_history:
        # Convert the start_time, end_time to a datetime object
        start_time_datetime = datetime.strptime(call['StartTimeUTC'], '%Y-%m-%dT%H:%M:%SZ')
        end_time_datetime = datetime.strptime(call['EndTimeUTC'], '%Y-%m-%dT%H:%M:%SZ')

        # Check if the start_time is within the last 30 days
        if start_time_datetime.replace(tzinfo=pytz.utc) >= x_days_ago:
            # Generate unique call hash
            hash_val = generate_unique_hash(call['deviceId'], call['CallbackNumber'], call['StartTimeUTC'],
                                            call['EndTimeUTC'])

            # Calculate MOSS Values For Audio and Video Streams (Minimum of Incoming and Outgoing)
            audio_metrics_incoming = call['Audio']['Incoming']
            audio_metrics_outgoing = call['Audio']['Outgoing']
            audio_moss = util.calculate_mos(audio_metrics_incoming['MaxJitter'], audio_metrics_outgoing['MaxJitter'],
                                            audio_metrics_incoming['PacketLossPercent'],
                                            audio_metrics_outgoing['PacketLossPercent'])

            video_metrics_incoming = call['Video']['Incoming']
            video_metrics_outgoing = call['Video']['Outgoing']
            video_moss = util.calculate_mos(video_metrics_incoming['MaxJitter'], video_metrics_outgoing['MaxJitter'],
                                            video_metrics_incoming['PacketLossPercent'],
                                            video_metrics_outgoing['PacketLossPercent'])

            # Calculate Max Audio and Video PacketLoss and Jitter
            audio_pkt_loss_max = max(audio_metrics_incoming['PacketLossPercent'],
                                     audio_metrics_outgoing['PacketLossPercent'])
            video_pkt_loss_max = max(video_metrics_incoming['PacketLossPercent'],
                                     video_metrics_outgoing['PacketLossPercent'])
            audio_jit_max = max(audio_metrics_incoming['MaxJitter'], audio_metrics_outgoing['MaxJitter'])
            video_jit_max = max(video_metrics_incoming['MaxJitter'], video_metrics_outgoing['MaxJitter'])

            # Insert a new call history entry, ignoring duplicates based on call_id
            update_statement = f"INSERT OR IGNORE INTO call_history (call_id, device_id, display_name, callback_number, remote_number, start_time, end_time, duration, disconnect_reason, a_mos, v_mos, a_pkt_loss_max, v_pkt_loss_max, a_jit_max, v_jit_max) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            c.execute(update_statement, (
                hash_val, call['deviceId'], call['DisplayName'], call['CallbackNumber'],
                call['RemoteNumber'],
                start_time_datetime.strftime('%Y-%m-%d %H:%M:%S'), end_time_datetime.strftime('%Y-%m-%d %H:%M:%S'),
                call['Duration'], call['DisconnectCauseType'], audio_moss, video_moss, str(audio_pkt_loss_max),
                str(video_pkt_loss_max), str(audio_jit_max), str(video_jit_max)))
        else:
            # Found entry > x days (due to chronological ordering, any remaining entries will also be > 30 days)
            break

    conn.commit()


def update_device_region(conn: sqlite3.Connection, device_id: str, region: str):
    """
    Update Device Region
    :param conn: DB connection object
    :param device_id: Device ID of device to update
    :param region: New Region value
    :return:
    """
    c = conn.cursor()

    # Update device region
    update_statement = (f"UPDATE devices SET region = ? WHERE device_id=?")
    c.execute(update_statement, (region, device_id,))
    conn.commit()


def delete_old_device_entries(conn: sqlite3.Connection, device_id: str):
    """
    Delete devices no longer returned in get devices call (ex: device removed, no xapi permissions anymore)
    :param conn: DB connection object
    :param device_id: Device ID of Device to Delete
    """
    c = conn.cursor()

    # Execute a DELETE statement to remove entries older than 30 days
    c.execute("""
        DELETE FROM devices
        WHERE devices.device_id = ?
    """, (device_id,))
    conn.commit()


def delete_old_call_entries(conn: sqlite3.Connection, x_days_ago: datetime):
    """
    Hard Delete Call History entries > 30 days old
    :param conn: DB connection object
    :param x_days_ago: x days ago UTC time stamp, removes data > x days
    """
    c = conn.cursor()

    # Execute a DELETE statement to remove entries older than 30 days
    c.execute("""
        DELETE FROM call_history
        WHERE start_time < ?
    """, (x_days_ago.strftime('%Y-%m-%d %H:%M:%S'),))
    conn.commit()


def close_connection(conn: sqlite3.Connection):
    """
    Close DB Connection
    :param conn: DB Connection
    """
    conn.close()


# If running this python file, create connection to database, create tables, and print out the results of queries of
# every table
if __name__ == "__main__":
    conn = create_connection(db_path)
    create_tables(conn)
    pprint(f"Devices Table: {query_all_devices(conn, '*')}")
    pprint(f"Call History Table: {query_all_call_history(conn)}")
    close_connection(conn)
