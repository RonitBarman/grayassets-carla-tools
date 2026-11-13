
CITY_INFO = {'Los Angeles': (34.05, -118.25, 'America/Los_Angeles'), 'New York': (40.71, -74.01, 'America/New_York'), 'Chicago': (41.88, -87.63, 'America/Chicago'), 'San Francisco': (37.77, -122.42, 'America/Los_Angeles'), 'London': (51.51, -0.13, 'Europe/London'), 'Paris': (48.86, 2.35, 'Europe/Paris'), 'Tokyo': (35.68, 139.69, 'Asia/Tokyo'), 'Beijing': (39.9, 116.4, 'Asia/Shanghai'), 'Sydney': (-33.87, 151.21, 'Australia/Sydney')}

def get_sun_angles(city_name: str, date_str: str, time_str: str):
    """
    Return sun azimuth and altitude (CARLA: sun_azimuth_angle, sun_altitude_angle)
    for a given city, date, and time.
    
    Args:
        city_name (str): Name of the city (must be in CITY_INFO dict).
        date_str (str): Date in "YYYY-MM-DD".
        time_str (str): Time in "HH:MM" (24h).
    
    Returns:
        dict with keys {"sun_azimuth_angle", "sun_altitude_angle"}
    """
    import pandas as pd
    import pvlib
    if city_name not in CITY_INFO:
        raise ValueError(f"City '{city_name}' not found. Available: {list(CITY_INFO.keys())}")
    lat, lon, tz = CITY_INFO[city_name]
    times = pd.date_range(f'{date_str} {time_str}', periods=1, freq='1H', tz=tz)
    solpos = pvlib.solarposition.get_solarposition(times, lat, lon)
    altitude = float(solpos['elevation'].iloc[0])
    azimuth = float(solpos['azimuth'].iloc[0])
    return {'sun_altitude_angle': altitude, 'sun_azimuth_angle': azimuth}
