from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv
from garminconnect import Garmin as GarminClient
from notion_client import Client as NotionClient

from src.helpers import get_garmin_client, get_notion_client

local_tz = pytz.timezone('America/Los_Angeles')

ACTIVITY_ICONS = {
    "Barre": "https://img.icons8.com/?size=100&id=66924&format=png&color=000000",
    "Breathwork": "https://img.icons8.com/?size=100&id=9798&format=png&color=000000",
    "Cardio": "https://img.icons8.com/?size=100&id=71221&format=png&color=000000",
    "Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Hiking": "https://img.icons8.com/?size=100&id=9844&format=png&color=000000",
    "Indoor Cardio": "https://img.icons8.com/?size=100&id=62779&format=png&color=000000",
    "Indoor Cycling": "https://img.icons8.com/?size=100&id=47443&format=png&color=000000",
    "Indoor Rowing": "https://img.icons8.com/?size=100&id=71098&format=png&color=000000",
    "Pilates": "https://img.icons8.com/?size=100&id=9774&format=png&color=000000",
    "Meditation": "https://img.icons8.com/?size=100&id=9798&format=png&color=000000",
    "Rowing": "https://img.icons8.com/?size=100&id=71491&format=png&color=000000",
    "Running": "https://img.icons8.com/?size=100&id=k1l1XFkME39t&format=png&color=000000",
    "Strength Training": "https://img.icons8.com/?size=100&id=107640&format=png&color=000000",
    "Stretching": "https://img.icons8.com/?size=100&id=djfOcRn1m_kh&format=png&color=000000",
    "Swimming": "https://img.icons8.com/?size=100&id=9777&format=png&color=000000",
    "Treadmill Running": "https://img.icons8.com/?size=100&id=9794&format=png&color=000000",
    "Walking": "https://img.icons8.com/?size=100&id=9807&format=png&color=000000",
    "Yoga": "https://img.icons8.com/?size=100&id=9783&format=png&color=000000",
}

def get_all_activities(garmin_client: GarminClient, limit: int = 1000) -> list[dict]:
    return garmin_client.get_activities(0, limit)

def format_activity_type(activity_type: str, activity_name: str = "") -> tuple[str, str]:
    formatted_type = activity_type.replace('_', ' ').title() if activity_type else "Unknown"
    activity_subtype = formatted_type
    activity_type = formatted_type

    activity_mapping = {
        "Barre": "Strength",
        "Indoor Cardio": "Cardio",
        "Indoor Cycling": "Cycling",
        "Indoor Rowing": "Rowing",
        "Speed Walking": "Walking",
        "Strength Training": "Strength",
        "Treadmill Running": "Run",
        "Running": "Run"
    }

    if formatted_type == "Rowing V2":
        activity_type = "Rowing"
    elif formatted_type in ["Yoga", "Pilates"]:
        activity_type = "Yoga/Pilates"
        activity_subtype = formatted_type

    if formatted_type in activity_mapping:
        activity_type = activity_mapping[formatted_type]
        activity_subtype = formatted_type

    if activity_name and "meditation" in activity_name.lower():
        return "Meditation", "Meditation"
    if activity_name and "barre" in activity_name.lower():
        return "Strength", "Barre"
    if activity_name and "stretch" in activity_name.lower():
        return "Stretching", "Stretching"

    return activity_type, activity_subtype

def format_entertainment(activity_name: str) -> str:
    return activity_name.replace('ENTERTAINMENT', 'Netflix')

def calculate_metrics(activity: dict) -> tuple[float, float, float]:
    distance_miles = (activity.get('distance') or 0) / 1609.34
    
    # Using raw elapsed duration to perfectly match Garmin's dashboard average pace
    duration = activity.get('duration') or 0
    time_moving_mins = duration / 60
    
    if distance_miles > 0:
        decimal_pace = time_moving_mins / distance_miles
    else:
        decimal_pace = 0
        
    if decimal_pace > 0:
        pace_minutes = int(decimal_pace)
        pace_seconds = (decimal_pace - pace_minutes) * 60
        pace_per_mile = pace_minutes + (pace_seconds / 100)
    else:
        pace_per_mile = 0
        
    return distance_miles, time_moving_mins, pace_per_mile

def parse_local_date(activity: dict) -> str:
    local_str = activity.get('startTimeLocal')
    if not local_str:
        return activity.get('startTimeGMT')
    naive_dt = datetime.strptime(local_str, '%Y-%m-%d %H:%M:%S')
    localized_dt = local_tz.localize(naive_dt)
    return localized_dt.isoformat()

def activity_exists(
    notion_client: NotionClient,
    database_id: str,
    activity_date: datetime,
    activity_type: str,
    activity_name: str,
) -> dict | None:
    lookup_type = "Stretching" if "stretch" in activity_name.lower() else activity_type
    lookup_min_date = activity_date - timedelta(minutes=5)
    lookup_max_date = activity_date + timedelta(minutes=5)

    query = notion_client.databases.query(
        database_id=database_id,
        filter={
            "and": [
                {"property": "Date", "date": {"on_or_after": lookup_min_date.isoformat()}},
                {"property": "Date", "date": {"on_or_before": lookup_max_date.isoformat()}},
                {"property": "Type", "select": {"equals": lookup_type}},
                {"property": "Name", "title": {"equals": activity_name}}
            ]
        }
    )
    results = query['results']
    return results[0] if results else None

def activity_needs_update(existing_activity: dict, new_activity: dict) -> bool:
    existing_props = existing_activity['properties']
    activity_name = new_activity.get('activityName', '').lower()
    activity_type, activity_subtype = format_activity_type(
        new_activity.get('activityType', {}).get('typeKey', 'Unknown'),
        activity_name
    )

    distance_miles, time_moving_mins, pace_per_mile = calculate_metrics(new_activity)

    ex_dist = existing_props.get('Distance in Miles', {}).get('number')
    ex_time = existing_props.get('Time Moving', {}).get('number')
    ex_pace = existing_props.get('Pace Per Mile', {}).get('number')
    
    type_prop = existing_props.get('Type', {})
    ex_type = type_prop.get('select', {}).get('name') if type_prop.get('select') else None

    return (
        ex_dist != round(distance_miles, 2) or
        ex_time != round(time_moving_mins, 2) or
        ex_pace != round(pace_per_mile, 2) or
        ex_type != activity_type
    )

def create_activity(notion_client: NotionClient, garmin_client: GarminClient, database_id: str, activity: dict) -> None:
    activity_date = parse_local_date(activity)
    activity_name = format_entertainment(activity.get('activityName', 'Unnamed Activity'))
    activity_type, activity_subtype = format_activity_type(
        activity.get('activityType', {}).get('typeKey', 'Unknown'),
        activity_name
    )
    activity_id = activity.get('activityId')

    distance_miles, time_moving_mins, pace_per_mile = calculate_metrics(activity)
    icon_url = ACTIVITY_ICONS.get(activity_subtype if activity_subtype != activity_type else activity_type)

    properties = {
        "Date": {"date": {"start": activity_date}},
        "Type": {"select": {"name": activity_type}},
        "Name": {"title": [{"text": {"content": activity_name}}]},
        "Distance in Miles": {"number": round(distance_miles, 2)},
        "Time Moving": {"number": round(time_moving_mins, 2)},
        "Pace Per Mile": {"number": round(pace_per_mile, 2)}
    }

    page = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }

    if icon_url:
        page["icon"] = {"type": "external", "external": {"url": icon_url}}

    # Pull and construct mile splits for inside the page
    splits_blocks = []
    if activity_type == "Run" and activity_id:
        try:
            splits_data = garmin_client.get_activity_splits(activity_id)
            laps = splits_data.get('lapDTOs', []) if isinstance(splits_data, dict) else splits_data
            
            if laps:
                splits_blocks.append({
                    "object": "block",
                    "type": "heading_3",
                    "heading_3": {
                        "rich_text": [{"type": "text", "text": {"content": "Mile Splits"}}]
                    }
                })
                
                lap_num = 1
                for lap in laps:
                    lap_dist_mi = lap.get('distance', 0) / 1609.34
                    lap_time_min = lap.get('duration', 0) / 60
                    
                    if lap_dist_mi > 0:
                        lap_pace = lap_time_min / lap_dist_mi
                        pace_min = int(lap_pace)
                        pace_sec = int((lap_pace - pace_min) * 60)
                        pace_str = f"{pace_min}:{pace_sec:02d}"
                    else:
                        pace_str = "0:00"
                        
                    text_content = f"Lap {lap_num} ({lap_dist_mi:.2f} mi): {pace_str}/mi"
                    
                    splits_blocks.append({
                        "object": "block",
                        "type": "bulleted_list_item",
                        "bulleted_list_item": {
                            "rich_text": [{"type": "text", "text": {"content": text_content}}]
                        }
                    })
                    lap_num += 1
                    
                page["children"] = splits_blocks
        except Exception:
            pass  # If Garmin fails to provide splits, continue creating the standard entry

    notion_client.pages.create(**page)

def update_activity(notion_client: NotionClient, existing_activity: dict, new_activity: dict) -> None:
    activity_name = new_activity.get('activityName', 'Unnamed Activity')
    activity_type, activity_subtype = format_activity_type(
        new_activity.get('activityType', {}).get('typeKey', 'Unknown'),
        activity_name
    )

    distance_miles, time_moving_mins, pace_per_mile = calculate_metrics(new_activity)
    icon_url = ACTIVITY_ICONS.get(activity_subtype if activity_subtype != activity_type else activity_type)

    properties = {
        "Type": {"select": {"name": activity_type}},
        "Name": {"title": [{"text": {"content": activity_name}}]},
        "Distance in Miles": {"number": round(distance_miles, 2)},
        "Time Moving": {"number": round(time_moving_mins, 2)},
        "Pace Per Mile": {"number": round(pace_per_mile, 2)}
    }

    update = {
        "page_id": existing_activity['id'],
        "properties": properties,
    }

    if icon_url:
        update["icon"] = {"type": "external", "external": {"url": icon_url}}

    notion_client.pages.update(**update)

def main():
    load_dotenv()

    garmin_client, garmin_configuration = get_garmin_client()
    notion_client, notion_dbs = get_notion_client()

    database_id = notion_dbs.activities

    activities = get_all_activities(garmin_client, garmin_configuration.activity_fetch_limit)

    for activity in activities:
        activity_date_str = parse_local_date(activity)
        activity_date: datetime = datetime.fromisoformat(activity_date_str)

        activity_name = format_entertainment(activity.get('activityName', 'Unnamed Activity'))
        activity_type, activity_subtype = format_activity_type(
            activity.get('activityType', {}).get('typeKey', 'Unknown'),
            activity_name
        )

        existing_activity = activity_exists(notion_client, database_id, activity_date, activity_type, activity_name)

        if existing_activity:
            if activity_needs_update(existing_activity, activity):
                update_activity(notion_client, existing_activity, activity)
        else:
            create_activity(notion_client, garmin_client, database_id, activity)

if __name__ == '__main__':
    main()
