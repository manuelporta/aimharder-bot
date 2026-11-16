#!/usr/bin/env python3
"""
AimHarder Bot
Automatically books classes based on a predefined schedule.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

import pytz

# Import shared utilities
from bot_utils import (
    send_telegram_notification,
    TIMEZONE, DEFAULT_BOX_NAME, DEFAULT_BOX_ID
)
from client import AimHarderClient



def print_and_notify_booking(box_name: str, class_name: str, display_time: str, full_date_str: str, status: str, err: str = ""):
    email = os.environ.get("EMAIL")
    if status == "CONFIRMED":
        print(f"✅ Successfully booked '{class_name}' at {display_time} on {full_date_str} for {email}")
        msg = (
            f"✅ <b>Booking CONFIRMED</b>\n"
            f"<b>Box:</b> {box_name}\n"
            f"<b>User:</b> {email}\n"
            f"<b>Class:</b> {class_name}\n"
            f"<b>Time:</b> {display_time}\n"
            f"<b>Date:</b> {full_date_str}"
        )
    elif status == "DRY_RUN":
        print(f"🔵 DRY RUN: Would book class '{class_name}' on {full_date_str} for {email}")
        msg = (
            f"🔵 <b>DRY RUN</b>\n"
            f"<b>Box:</b> {box_name}\n"
            f"<b>User:</b> {email}\n"
            f"<b>Class:</b> {class_name}\n"
            f"<b>Time:</b> {display_time}\n"
            f"<b>Date:</b> {full_date_str}"
        )
    else:
        print(f"❌ Booking failed for {email}.\n{err}")
        msg = (
            f"❌ <b>Booking FAILED</b>\n"
            f"<b>Box:</b> {box_name}\n"
            f"<b>User:</b> {email}\n"
            f"<b>Class:</b> {class_name}\n"
            f"<b>Time:</b> {display_time}\n"
            f"<b>Date:</b> {full_date_str}\n"
            f"<b>Reason:</b> {err}"
        )
    send_telegram_notification(msg)

def process_booking(client: AimHarderClient, class_info: dict, target_date: datetime, dry_run: bool = False) -> bool:
    class_id = class_info.get("id", class_info.get("classId", class_info.get("sessionId")))
    class_name = class_info.get("className", class_info.get("name", "Unknown"))
    if not class_id:
        print("❌ Could not determine class ID from class info")
        return False
        
    display_time = class_info.get("time", class_info.get("startTime", "Unknown"))
    if isinstance(display_time, str) and "_" in display_time:
        display_time = display_time.split("_")[0]
        if len(display_time) == 4 and display_time.isdigit():
            display_time = f"{display_time[:2]}:{display_time[2:]}"
            
    days_es = {
        "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
        "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo"
    }
    day_name = days_es.get(target_date.strftime("%A"), target_date.strftime("%A"))
    full_date_str = f"{day_name} {target_date.strftime('%d/%m/%Y')}"

    if dry_run:
        print_and_notify_booking(client.box_name, class_name, display_time, full_date_str, "DRY_RUN")
        return True

    try:
        resp = client.book_class(class_id, target_date)
        # print(f"📡 API response: {resp}")
        book_state = resp.get("bookState")
        if book_state == 1:
            print_and_notify_booking(client.box_name, class_name, display_time, full_date_str, "CONFIRMED")
            return True
        else:
            reason = resp.get("errorMssg", resp.get("bookError", resp.get("error", ""))) or f"bookState={book_state}"
            print_and_notify_booking(client.box_name, class_name, display_time, full_date_str, "FAILED", reason)
            return False
    except Exception as e:
        print_and_notify_booking(client.box_name, class_name, display_time, full_date_str, "FAILED", str(e))
        return False

def find_matching_class(classes: list, target_time: str, target_name: str) -> Optional[dict]:
    """
    Find a class matching the target time and name.
    """
    # Normalize target time: "19:00" -> "1900"
    target_time_normalized = target_time.replace(":", "")
    
    for box_class in classes:
        # Common field names for time
        class_time = box_class.get("timeid", box_class.get("time", box_class.get("startTime", "")))
        # Common field names for class name
        class_name = box_class.get("className", box_class.get("name", box_class.get("activity", "")))
        
        # Normalize API time format: "0700_60" -> "0700", "07:00" -> "0700"
        if isinstance(class_time, str):
            # Handle "0700_60" format (time_duration)
            if "_" in class_time:
                class_time_normalized = class_time.split("_")[0]
            else:
                # Handle "07:00" or "07:00:00" format
                class_time_normalized = class_time.replace(":", "")[:4]
        else:
            class_time_normalized = str(class_time)
        
        time_match = class_time_normalized == target_time_normalized
        name_match = target_name.lower() in class_name.lower()
        
        if time_match and name_match:
            print(f"✅ Found matching class: {class_name} at {class_time}")
            
            # Check if already booked
            ocupation = box_class.get("ocupation", None)
            if ocupation is None:
                raise ValueError(f"Class data missing 'ocupation' field: {box_class}")
            limit = box_class.get("limit", None)
            if limit is None:
                raise ValueError(f"Class data missing 'limit' field: {box_class}")
            
            if ocupation >= limit:
                print(f"⚠️ Clase de '{class_name}' a las {class_time} LLENA.")
                box_class["_is_already_booked"] = True # Mark for caller
            
            return box_class
    
    return None

def book_class(args: argparse.Namespace):
    # Get credentials from environment
    email = os.environ.get("EMAIL")
    password = os.environ.get("PASSWORD")
    
    if not email or not password:
        print("❌ Missing credentials. Set EMAIL and PASSWORD environment variables.")
        sys.exit(1)
    
    
    # Read box name and ID, preferring arguments, then environment variables, then schedule JSON.
    env_box_id = os.environ.get("BOX_ID") or os.environ.get("box-id")
    env_box_name = os.environ.get("BOX_NAME") or os.environ.get("box-name")
    
    box_id_val = args.box_id or (int(env_box_id) if env_box_id else None) or DEFAULT_BOX_ID
    box_name = args.box_name or env_box_name or DEFAULT_BOX_NAME
    box_id = int(box_id_val) if box_id_val else 0

    try:
        client = AimHarderClient(email, password, box_name, box_id)
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)

    
    # Determine target date
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)
    target_date = now + timedelta(days=args.days_ahead)
    day_name = target_date.strftime("%A")
    
    print(f"📅 Target date: {target_date.strftime('%Y-%m-%d')} ({day_name})")

    # Process booking
    print(f"\n🚀 Processing Box: {box_name} (ID: {box_id})")
    
    target_time = f"{args.target_hour:02d}:{args.target_minute:02d}"
    target_class = "CROSSFIT"
    
    print(f"🎯 Target: {target_class} at {target_time}")
    
    try:
        classes = client.list_classes(target_date)
    except Exception as e:
        print(f"❌ Error fetching classes: {e}")
        classes = []
        
    if not classes:
        print(f"❌ No classes found for {target_date.strftime('%Y-%m-%d')}")
        sys.exit(1)
        
    # Find matching class
    matching_class = find_matching_class(classes, target_time, target_class)
    if not matching_class:
        print(f"❌ No matching class found for {target_class} at {target_time}")
        sys.exit(1)
        
    # Check if already booked
    if matching_class.get("_is_already_booked"):
        print(f"ℹ️ Skipping booking: Already booked.")
        return
        

    # Book the class
    success = process_booking(client, matching_class, target_date, dry_run=args.dry_run)

    if success:
        print("\n🏁 Booking completed successfully.")
    else:
        print("\n❌ Booking failed. Check the logs above for details.")

def main():

    parser = argparse.ArgumentParser(description="AimHarder Bot")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually book, just show what would be booked")
    parser.add_argument("--days-ahead", dest="days_ahead", type=int, default=int(os.environ.get("DAYS_AHEAD", 3)), help="Book for N days ahead (default: 3)")
    parser.add_argument("--box-name", type=str, default=None, help="Optional override for box name (subdomain)")
    parser.add_argument("--box-id", type=int, default=None, help="Optional override for box ID")
    parser.add_argument("--target-hour", type=int, default=int(os.environ.get("TARGET_HOUR", 20)), help="Target class start hour in Madrid time (0-23), default: 20")
    parser.add_argument("--target-minute", type=int, default=int(os.environ.get("TARGET_MINUTE", 0)), help="Target minute to run (0-59), default: 0")
    args = parser.parse_args()

    book_class(args)


if __name__ == "__main__":
    main()
