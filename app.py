#!/usr/bin/env python3
"""
Valentine's Day Topographical Map STL Generator
MVP - Local Development Version
"""

from flask import Flask, render_template, request, send_file, jsonify
import requests
import trimesh
import numpy as np
from shapely.geometry import Polygon, Point
from shapely.ops import transform
import math
import os
import time
from datetime import datetime

app = Flask(__name__)

# Configuration
OUTPUT_DIR = 'output'
BASE_SIZE_MM = 150  # 6 inches = ~150mm
DEFAULT_BUILDING_HEIGHT = 10  # meters (if not specified in OSM)
HEART_SIZE = 8  # mm diameter
VERTICAL_EXAGGERATION = 1.88  # Multiply heights by this factor (matches map2model scaling)

# Ensure output directory exists
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================================
# OVERPASS API RETRY HELPER
# ============================================================================

def query_overpass_with_retry(query, max_retries=3, base_delay=2):
    """
    Query Overpass API with exponential backoff retry logic.

    Args:
        query: Overpass QL query string
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds (doubles each retry)

    Returns:
        dict: API response data or None on failure
    """
    # Try multiple Overpass servers (overpass-api.de has been unreliable)
    servers = [
        'https://overpass.kumi.systems/api/interpreter',
        'https://overpass-api.de/api/interpreter',
        'https://overpass.openstreetmap.ru/api/interpreter',
    ]

    for attempt in range(max_retries):
        server = servers[attempt % len(servers)]
        try:
            response = requests.post(
                server,
                data={'data': query},
                timeout=60  # Increased from 30s
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"Timeout from {server} - trying next server in {delay}s...")
                time.sleep(delay)
            else:
                print(f"Failed after {max_retries} attempts (timeout)")
                return None
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 504:  # Gateway Timeout
                if attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    print(f"504 Gateway Timeout from {server} - trying next server in {delay}s...")
                    time.sleep(delay)
                else:
                    print(f"Failed after {max_retries} attempts (504 error)")
                    return None
            else:
                print(f"HTTP Error: {e}")
                return None
        except Exception as e:
            print(f"Error querying Overpass API: {e}")
            return None

    return None


# ============================================================================
# MODEL CONFIGURATION CLASS
# ============================================================================

class ModelConfig:
    """Configuration class for all model generation parameters"""

    def __init__(self, config_dict=None):
        """Initialize with defaults, optionally override with config_dict"""
        # Buildings
        self.building_color = "#B8B8B8"  # Light gray (map2model optimized)
        self.building_roofs = False
        self.building_force_basic = False
        self.building_scale = 1.0
        self.building_min_height_mm = 1.0
        self.building_min_area_m2 = 0.5  # Reduced from 1.0 to catch smaller buildings

        # Roads
        self.road_color = "#262626"  # Dark gray (map2model optimized - more printable than black)
        self.road_height_mm = 0.60
        self.road_depth_mm = 0.00
        self.road_width_multiplier = 1.0
        self.road_include_footpaths = False
        self.road_underground = False
        self.road_types = ["motorway", "trunk", "primary", "secondary"]
        self.aeroway_types = []
        self.railway_types = []

        # Water
        self.water_color = "#1479C6"  # Blue
        self.water_height_mm = 0.40
        self.water_depth_mm = 0.80
        self.water_min_area_m2 = 100.0  # Rivers and major water features (reduced from 500 to catch rivers)
        self.water_include_sea = False
        self.water_cut_out = False
        self.water_base = False
        self.waterway_types = []

        # Parks
        self.park_color = "#0FE300"  # Bright green (map2model optimized)
        self.park_height_mm = 0.80  # Taller than roads (0.6mm) to be visible
        self.park_min_area_m2 = 0.1  # Reduced from 1.0 to catch smaller green spaces

        # Footpaths
        self.footpath_enabled = False
        self.footpath_color = "#000000"  # Black
        self.footpath_height_mm = 0.30
        self.footpath_width_m = 1.5  # Width in meters for footpaths

        # Export options
        self.split_export = False  # Export separate STLs for each feature in a zip

        # Override with provided config
        if config_dict:
            for key, value in config_dict.items():
                if hasattr(self, key):
                    setattr(self, key, value)


# ============================================================================
# COORDINATE UTILITIES
# ============================================================================

def create_bbox(lat, lon, radius_meters):
    """
    Create a bounding box around a lat/lon point with given radius.
    Returns [min_lat, min_lon, max_lat, max_lon]
    """
    # Earth radius in meters
    earth_radius = 6371000.0

    # Angular distance in radians
    angular_distance = radius_meters / earth_radius

    # Convert to radians
    lat_rad = math.radians(lat)
    lon_rad = math.radians(lon)

    # Calculate bounds
    lat_delta = math.degrees(angular_distance)
    lon_delta = math.degrees(angular_distance / math.cos(lat_rad))

    min_lat = lat - lat_delta
    max_lat = lat + lat_delta
    min_lon = lon - lon_delta
    max_lon = lon + lon_delta

    return [min_lat, min_lon, max_lat, max_lon]


def lat_lon_to_meters(lat, lon, center_lat, center_lon):
    """
    Convert lat/lon to meters relative to center point.
    Returns (x, y) in meters.
    """
    # Approximate conversion (good enough for small areas)
    earth_radius = 6371000.0

    lat_rad = math.radians(lat)
    center_lat_rad = math.radians(center_lat)

    x = (lon - center_lon) * math.cos(center_lat_rad) * earth_radius * math.pi / 180
    y = (lat - center_lat) * earth_radius * math.pi / 180

    return (x, y)


def create_circular_boundary(radius_meters, scale_factor, resolution=64):
    """
    Create a circular polygon boundary in the scaled coordinate system.
    Returns a Shapely Polygon representing the circular query area.
    """
    # Create circle in meters, then scale to mm
    angles = np.linspace(0, 2*np.pi, resolution)
    x = radius_meters * np.cos(angles) * scale_factor
    y = radius_meters * np.sin(angles) * scale_factor
    points = [[x[i], y[i]] for i in range(len(angles))]
    return Polygon(points)


def clip_polygon_to_circle(polygon, circle_boundary):
    """
    Clip a polygon to the circular boundary.
    Returns the clipped polygon, or None if no intersection.
    """
    try:
        if not polygon.is_valid:
            polygon = polygon.buffer(0)  # Fix invalid geometry

        intersection = polygon.intersection(circle_boundary)

        if intersection.is_empty:
            return None

        # Handle MultiPolygon results
        # IMPORTANT: Return ALL pieces, not just the largest!
        # Discarding pieces causes missing water/park sections
        if intersection.geom_type == 'MultiPolygon':
            return intersection  # Return full MultiPolygon with all pieces
        elif intersection.geom_type == 'Polygon':
            return intersection
        else:
            return None
    except Exception as e:
        return None


def clip_linestring_to_circle(path_coords, circle_boundary):
    """
    Clip a line path to the circular boundary.
    Returns list of clipped path segments, or empty list if no intersection.
    """
    from shapely.geometry import LineString, MultiLineString

    try:
        if len(path_coords) < 2:
            return []

        linestring = LineString(path_coords)
        intersection = linestring.intersection(circle_boundary)

        if intersection.is_empty:
            return []

        # Handle different return types
        if intersection.geom_type == 'LineString':
            return [list(intersection.coords)]
        elif intersection.geom_type == 'MultiLineString':
            return [list(ls.coords) for ls in intersection.geoms]
        elif intersection.geom_type == 'Point':
            # Intersection is just a point, skip it
            return []
        else:
            return []
    except Exception as e:
        return []


# ============================================================================
# POLYGON SCALING (for tapered structures like towers)
# ============================================================================

def scale_polygon_from_centroid(coords, scale_factor):
    """
    Scale a polygon footprint from its centroid.
    Used for creating tapered tower structures.

    Args:
        coords: List of (lat, lon) tuples
        scale_factor: Scaling factor (< 1.0 = shrink, > 1.0 = grow)

    Returns:
        Scaled list of (lat, lon) tuples
    """
    if not coords or len(coords) < 3:
        return coords

    # Calculate centroid
    centroid_lat = sum(c[0] for c in coords) / len(coords)
    centroid_lon = sum(c[1] for c in coords) / len(coords)

    # Scale each point from centroid
    scaled_coords = []
    for lat, lon in coords:
        # Vector from centroid to point
        dlat = lat - centroid_lat
        dlon = lon - centroid_lon

        # Scale the vector
        new_lat = centroid_lat + (dlat * scale_factor)
        new_lon = centroid_lon + (dlon * scale_factor)

        scaled_coords.append((new_lat, new_lon))

    return scaled_coords


# ============================================================================
# BUILDING HEIGHT INFERENCE
# ============================================================================

def infer_building_min_height(tags):
    """
    Extract minimum height (starting elevation) from OSM tags.
    This is used for building:part elements that don't start at ground level.
    Returns min_height in meters (defaults to 0.0).
    """
    if 'min_height' in tags:
        try:
            return float(tags['min_height'])
        except:
            pass

    if 'building:min_height' in tags:
        try:
            return float(tags['building:min_height'])
        except:
            pass

    if 'building:min_level' in tags:
        try:
            min_level = float(tags['building:min_level'])
            return min_level * 3.0  # Assume 3m per level
        except:
            pass

    return 0.0  # Default: starts at ground level


def infer_building_height(tags):
    """
    Intelligently infer building height from OSM tags.
    Returns height in meters.
    """
    # First check explicit height tags
    if 'height' in tags:
        try:
            return float(tags['height'])
        except:
            pass

    if 'building:height' in tags:
        try:
            return float(tags['building:height'])
        except:
            pass

    if 'building:levels' in tags:
        try:
            levels = float(tags['building:levels'])
            return levels * 3.0  # Assume 3m per level
        except:
            pass

    # Infer from building type
    building_type = tags.get('building', '').lower()

    # Religious buildings (often have tall spires/towers)
    if building_type in ['cathedral', 'church', 'chapel', 'mosque', 'synagogue', 'temple']:
        return 50.0

    if tags.get('amenity') == 'place_of_worship':
        return 35.0

    # Educational/institutional buildings
    if building_type in ['university', 'college', 'school']:
        return 40.0

    # Towers and tall structures
    if building_type in ['tower']:
        return 60.0

    # Government/civic buildings
    if building_type in ['government', 'public', 'civic']:
        return 25.0

    # Commercial/office buildings
    if building_type in ['commercial', 'office', 'retail']:
        return 20.0

    # Hotels
    if building_type in ['hotel']:
        return 25.0

    # Apartments (taller than houses)
    if building_type in ['apartments', 'residential']:
        return 15.0

    # Named landmarks (likely important/tall)
    if 'name' in tags and len(tags['name']) > 0:
        # If it has a name but no type info, assume it's notable → taller
        return 20.0

    # Default for regular buildings
    return DEFAULT_BUILDING_HEIGHT


# ============================================================================
# OPENSTREETMAP DATA FETCHING
# ============================================================================

def query_osm_buildings(lat, lon, radius_meters=300):
    """
    Query OpenStreetMap Overpass API for buildings in area.
    Returns list of building dictionaries with footprint and height.
    """
    print(f"Querying OSM for buildings near ({lat}, {lon}) with radius {radius_meters}m...")

    bbox = create_bbox(lat, lon, radius_meters)

    # Overpass API query - include building:part for complex structures
    query = f"""
    [out:json][timeout:25];
    (
      way["building"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      relation["building"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["building:part"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
    );
    out body;
    >;
    out skel qt;
    """

    data = query_overpass_with_retry(query)
    if data is None:
        return []

    # Parse response
    nodes = {}
    buildings = []
    building_part_nodes = set()  # Track nodes used by building:part elements

    # First pass: collect all nodes
    for element in data.get('elements', []):
        if element['type'] == 'node':
            nodes[element['id']] = (element['lat'], element['lon'])

    # Second pass: identify building:part nodes
    for element in data.get('elements', []):
        if element['type'] == 'way' and 'tags' in element and 'building:part' in element['tags']:
            building_part_nodes.update(element['nodes'])

    # Third pass: process main buildings
    for element in data.get('elements', []):
        if element['type'] == 'way' and 'tags' in element and 'building' in element['tags']:
            tags = element['tags']
            building_type = tags.get('building', '')

            # Check if this building has building:part children
            building_nodes = set(element['nodes'])
            has_parts = len(building_nodes & building_part_nodes) > 0

            # CRITICAL: Skip parent building if building:part children exist
            # This prevents extruding the wide base footprint when tower sections handle geometry
            if has_parts:
                # Skip if it's a tower/spire/chimney OR if it has many nodes (large complex building)
                if building_type in ['tower', 'spire', 'chimney'] or len(element['nodes']) > 50:
                    # Skip parent - the building:part elements define the actual geometry
                    continue

            # Get building height using intelligent inference
            height = infer_building_height(tags)

            # Get footprint coordinates
            node_ids = element['nodes']
            footprint = []

            for node_id in node_ids:
                if node_id in nodes:
                    footprint.append(nodes[node_id])

            # Only add if we have a valid footprint (at least 3 points)
            if len(footprint) >= 3:
                buildings.append({
                    'footprint': footprint,
                    'height': height,
                    'min_height': 0.0,  # Towers start at ground level
                    'tags': tags,
                    'is_building_part': True  # Mark as tower/chimney - should get roof
                })

    # Fourth pass: process building relations (multipolygons with courtyards)
    # Store ways by ID for relation processing
    ways_by_id = {}
    for element in data.get('elements', []):
        if element['type'] == 'way':
            ways_by_id[element['id']] = element

    for element in data.get('elements', []):
        if element['type'] == 'relation' and 'tags' in element and 'building' in element['tags']:
            tags = element['tags']
            height = infer_building_height(tags)

            # Find outer and inner ways of the multipolygon
            outer_ways = []
            inner_ways = []

            for member in element.get('members', []):
                if member.get('type') == 'way':
                    way_id = member.get('ref')
                    if way_id in ways_by_id:
                        way = ways_by_id[way_id]
                        # Get coordinates for this way
                        coords = []
                        for node_id in way.get('nodes', []):
                            if node_id in nodes:
                                coords.append(nodes[node_id])

                        if len(coords) >= 3:
                            if member.get('role') == 'outer':
                                outer_ways.append(coords)
                            elif member.get('role') == 'inner':
                                inner_ways.append(coords)

            # Only add if we have outer way(s)
            if outer_ways:
                if inner_ways:
                    print(f"\n=== RELATION BUILDING with COURTYARDS ===")
                    print(f"Type: {tags.get('building', 'unknown')}")
                    print(f"Name: {tags.get('name', 'unnamed')}")
                    print(f"Amenity: {tags.get('amenity', 'none')}")
                    print(f"Inner ways (courtyards): {len(inner_ways)}")
                    print(f"All tags: {tags}")
                buildings.append({
                    'footprint': outer_ways[0],  # Main outer footprint
                    'inner_footprints': inner_ways,  # Courtyards/holes
                    'height': height,
                    'min_height': 0.0,  # Main buildings start at ground level
                    'tags': tags,
                    'is_building_part': False  # Main building - no roof for base
                })

    # Fifth pass: process building:part elements (towers, complex structures)
    # First sub-pass: find max height to calculate taper
    max_part_height = 0.0
    part_heights = []
    part_elements = []

    for element in data.get('elements', []):
        if element['type'] == 'way' and 'tags' in element and 'building:part' in element['tags']:
            tags = element['tags']
            height = infer_building_height(tags)
            min_height = infer_building_min_height(tags)

            part_heights.append(height)
            if height > max_part_height:
                max_part_height = height

            part_elements.append((element, height, min_height))

    # Second sub-pass: process parts with taper scaling
    for element, height, min_height in part_elements:
        tags = element['tags']

        # Get footprint coordinates
        node_ids = element['nodes']
        footprint = []

        for node_id in node_ids:
            if node_id in nodes:
                footprint.append(nodes[node_id])

        # Scale footprint for tower structures (in lat/lon space, before meter conversion)
        if len(footprint) >= 3 and max_part_height > 0 and min_height > 0:
            # Calculate taper factor based on section's base elevation
            # At ground (0m) = 100%, at tower top (max_part_height) = 20%
            taper_factor = 1.0 - (min_height / max_part_height) * 0.80
            taper_factor = max(0.2, min(1.0, taper_factor))

            # Scale footprint from centroid
            footprint = scale_polygon_from_centroid(footprint, taper_factor)

        # Only add if we have a valid footprint (at least 3 points)
        if len(footprint) >= 3:
            buildings.append({
                'footprint': footprint,  # Pre-scaled footprint
                'height': height,
                'min_height': min_height,
                'tags': tags,
                'is_building_part': True
            })

    # Count regular buildings vs parts
    regular = sum(1 for b in buildings if not b.get('is_building_part', False))
    parts = sum(1 for b in buildings if b.get('is_building_part', False))
    print(f"Found {len(buildings)} total structures: {regular} buildings + {parts} building:part elements")
    if part_heights:
        print(f"Building:part heights: min={min(part_heights):.1f}m, max={max(part_heights):.1f}m, avg={sum(part_heights)/len(part_heights):.1f}m")
    return buildings


def query_osm_water(lat, lon, radius_meters=300, config=None):
    """Query OpenStreetMap for water features"""
    if config is None:
        config = ModelConfig()

    print(f"Querying OSM for water features near ({lat}, {lon}) with radius {radius_meters}m...")

    bbox = create_bbox(lat, lon, radius_meters)

    # Query for VISIBLE water features only (specific types only, no catch-alls)
    query = f"""
    [out:json][timeout:25];
    (
      way["natural"="water"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["waterway"="river"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["waterway"="canal"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["waterway"="stream"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      relation["natural"="water"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      relation["waterway"="river"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      relation["waterway"="canal"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
    );
    out body;
    >;
    out skel qt;
    """

    data = query_overpass_with_retry(query)
    if data is None:
        return []

    nodes = {}
    water_features = []

    for element in data.get('elements', []):
        if element['type'] == 'node':
            nodes[element['id']] = (element['lat'], element['lon'])

    # First pass: collect all ways (for both direct ways and relation members)
    ways = {}
    for element in data.get('elements', []):
        if element['type'] == 'way':
            ways[element['id']] = element

    # Second pass: process both direct ways and relations
    for element in data.get('elements', []):
        if element['type'] == 'way' and 'tags' in element:
            node_ids = element['nodes']
            footprint = [nodes[nid] for nid in node_ids if nid in nodes]

            if len(footprint) >= 3:
                # Store waterway type for different filtering rules
                tags = element['tags']
                water_type = tags.get('waterway', tags.get('natural', 'unknown'))
                water_features.append({
                    'footprint': footprint,
                    'water_type': water_type  # river, canal, water, etc.
                })

        # Process relations (multipolygon rivers/lakes)
        elif element['type'] == 'relation' and 'tags' in element:
            tags = element['tags']
            water_type = tags.get('waterway', tags.get('natural', 'unknown'))

            # Process outer members of the relation
            for member in element.get('members', []):
                if member.get('role') == 'outer' and member.get('type') == 'way':
                    way_id = member.get('ref')
                    if way_id in ways:
                        way = ways[way_id]
                        node_ids = way.get('nodes', [])
                        footprint = [nodes[nid] for nid in node_ids if nid in nodes]

                        if len(footprint) >= 3:
                            water_features.append({
                                'footprint': footprint,
                                'water_type': water_type  # from relation tags
                            })

    print(f"Found {len(water_features)} water features")
    return water_features


def query_osm_parks(lat, lon, radius_meters=300):
    """Query OpenStreetMap for parks and green areas"""
    print(f"Querying OSM for parks/green areas near ({lat}, {lon}) with radius {radius_meters}m...")

    bbox = create_bbox(lat, lon, radius_meters)

    query = f"""
    [out:json][timeout:25];
    (
      way["leisure"="park"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["leisure"="garden"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["leisure"="recreation_ground"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["leisure"="playground"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["leisure"="pitch"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["landuse"="forest"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["landuse"="grass"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["landuse"="meadow"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["landuse"="village_green"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["landuse"="recreation_ground"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["natural"="wood"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["natural"="grassland"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      relation["leisure"="park"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
    );
    out body;
    >;
    out skel qt;
    """

    data = query_overpass_with_retry(query)
    if data is None:
        return []

    nodes = {}
    parks = []

    for element in data.get('elements', []):
        if element['type'] == 'node':
            nodes[element['id']] = (element['lat'], element['lon'])

    for element in data.get('elements', []):
        if element['type'] == 'way' and 'tags' in element:
            node_ids = element['nodes']
            footprint = [nodes[nid] for nid in node_ids if nid in nodes]

            if len(footprint) >= 3:
                parks.append({'footprint': footprint})

    print(f"Found {len(parks)} parks/green areas")
    return parks


def query_osm_footpaths(lat, lon, radius_meters=300):
    """Query OpenStreetMap for footpaths, pedestrian ways, and walking paths"""
    print(f"Querying OSM for footpaths near ({lat}, {lon}) with radius {radius_meters}m...")

    bbox = create_bbox(lat, lon, radius_meters)

    query = f"""
    [out:json][timeout:25];
    (
      way["highway"="footway"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["highway"="path"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["highway"="pedestrian"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
      way["highway"="steps"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
    );
    out body;
    >;
    out skel qt;
    """

    data = query_overpass_with_retry(query)
    if data is None:
        return []

    nodes = {}
    footpaths = []

    for element in data.get('elements', []):
        if element['type'] == 'node':
            nodes[element['id']] = (element['lat'], element['lon'])

    for element in data.get('elements', []):
        if element['type'] == 'way' and 'tags' in element:
            node_ids = element['nodes']
            path = [nodes[nid] for nid in node_ids if nid in nodes]

            # Footpaths are linear features, need at least 2 points
            if len(path) >= 2:
                footpaths.append({'path': path})

    print(f"Found {len(footpaths)} footpaths")
    return footpaths


def query_osm_roads(lat, lon, radius_meters=300, config=None):
    """Query OpenStreetMap for major roads"""
    if config is None:
        config = ModelConfig()

    print(f"Querying OSM for roads near ({lat}, {lon}) with radius {radius_meters}m...")

    bbox = create_bbox(lat, lon, radius_meters)

    # Only major roads to avoid clutter
    query = f"""
    [out:json][timeout:25];
    (
      way["highway"~"motorway|trunk|primary|secondary"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
    );
    out body;
    >;
    out skel qt;
    """

    data = query_overpass_with_retry(query)
    if data is None:
        return []

    nodes = {}
    roads = []

    for element in data.get('elements', []):
        if element['type'] == 'node':
            nodes[element['id']] = (element['lat'], element['lon'])

    for element in data.get('elements', []):
        if element['type'] == 'way' and 'tags' in element and 'highway' in element['tags']:
            node_ids = element['nodes']
            path = [nodes[nid] for nid in node_ids if nid in nodes]

            if len(path) >= 2:
                roads.append({'path': path})

    print(f"Found {len(roads)} major roads")
    return roads


# ============================================================================
# 3D MESH GENERATION
# ============================================================================

def create_pyramid_roof(polygon, base_z, roof_height):
    """
    Create a pyramid roof mesh for a building polygon.
    Returns trimesh object or None if creation fails.
    """
    try:
        # Calculate centroid for roof apex
        centroid = polygon.centroid
        apex = [centroid.x, centroid.y, base_z + roof_height]

        # Get exterior coordinates
        coords = list(polygon.exterior.coords[:-1])
        n_points = len(coords)

        if n_points < 3:
            return None

        # Create vertices: base perimeter + apex
        vertices = []
        for x, y in coords:
            vertices.append([x, y, base_z])
        vertices.append(apex)

        apex_idx = n_points

        # Create faces: triangular sides from each edge to apex
        faces = []
        for i in range(n_points):
            next_i = (i + 1) % n_points
            faces.append([i, next_i, apex_idx])

        # Create mesh
        roof_mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces))
        return roof_mesh
    except Exception as e:
        return None


def create_building_meshes(buildings, center_lat, center_lon, scale_factor=1.0, circle_boundary=None, config=None):
    """
    Convert building footprints to 3D meshes with configurable parameters.
    Returns list of trimesh objects.
    """
    if config is None:
        config = ModelConfig()

    meshes = []

    for i, building in enumerate(buildings):
        try:
            # Convert footprint to meters
            footprint_meters = []
            for lat, lon in building['footprint']:
                x, y = lat_lon_to_meters(lat, lon, center_lat, center_lon)
                footprint_meters.append([x * scale_factor, y * scale_factor])

            # Create polygon
            if len(footprint_meters) < 3:
                continue

            # Handle inner footprints (courtyards/holes) if present
            inner_footprints_meters = []
            if 'inner_footprints' in building:
                for inner in building['inner_footprints']:
                    inner_coords = []
                    for lat, lon in inner:
                        x, y = lat_lon_to_meters(lat, lon, center_lat, center_lon)
                        inner_coords.append([x * scale_factor, y * scale_factor])
                    if len(inner_coords) >= 3:
                        inner_footprints_meters.append(inner_coords)

            # Create polygon with holes
            if inner_footprints_meters:
                polygon = Polygon(footprint_meters, inner_footprints_meters)
                building_name = building.get('tags', {}).get('name', 'unnamed')
                print(f"\n=== CREATING POLYGON with HOLES ===")
                print(f"Building {i}: {building_name}")
                print(f"Inner footprints provided: {len(inner_footprints_meters)}")
                print(f"Polygon interiors after creation: {len(polygon.interiors)}")
                print(f"Polygon is valid: {polygon.is_valid}")
            else:
                polygon = Polygon(footprint_meters)

            # Skip invalid polygons
            if not polygon.is_valid or polygon.is_empty:
                continue

            # Clip to circular boundary if provided
            if circle_boundary:
                polygon = clip_polygon_to_circle(polygon, circle_boundary)
                if polygon is None:
                    continue

            # Simplify polygon to remove degenerate edges (helps with thin slivers from clipping)
            # Use lighter simplification for buildings with courtyards to preserve holes
            had_interiors_before = len(polygon.interiors) if polygon.interiors else 0
            simplify_tolerance = 0.005 if polygon.interiors else 0.01  # Minimal simplification - preserve detail
            polygon = polygon.simplify(simplify_tolerance, preserve_topology=True)
            had_interiors_after = len(polygon.interiors) if polygon.interiors else 0

            if had_interiors_before > 0:
                print(f"  Courtyards before simplify: {had_interiors_before}, after: {had_interiors_after}")

            # Validate polygon after simplification
            if not polygon.is_valid or polygon.is_empty or polygon.area < 0.5:
                continue

            # Extrude to 3D with vertical exaggeration
            height_meters = building['height']
            min_height_meters = building.get('min_height', 0.0)

            height_mm = height_meters * scale_factor * VERTICAL_EXAGGERATION
            min_height_mm = min_height_meters * scale_factor * VERTICAL_EXAGGERATION

            # Enforce minimum printable height for the thickness (not the total height)
            thickness_mm = height_mm - min_height_mm
            if thickness_mm < config.building_min_height_mm:
                thickness_mm = config.building_min_height_mm

            height = thickness_mm

            # Buildings sit at their min_height (building:part elements can start elevated)
            building_base_z = min_height_mm

            # Manual extrusion to handle holes/courtyards properly
            vertices = []
            faces = []
            vertex_offset = 0

            # Process exterior ring
            exterior_coords = list(polygon.exterior.coords[:-1])
            n_exterior = len(exterior_coords)

            # Add bottom and top vertices (straight extrusion)
            for x, y in exterior_coords:
                vertices.append([x, y, building_base_z])
            for x, y in exterior_coords:
                vertices.append([x, y, building_base_z + height])

            # Exterior side faces
            for idx in range(n_exterior):
                next_idx = (idx + 1) % n_exterior
                v1 = vertex_offset + idx
                v2 = vertex_offset + next_idx
                v3 = vertex_offset + n_exterior + idx
                v4 = vertex_offset + n_exterior + next_idx
                faces.append([v1, v2, v3])
                faces.append([v2, v4, v3])

            vertex_offset += n_exterior * 2

            # Process interior rings (holes/courtyards)
            interior_vertex_counts = []
            for interior in polygon.interiors:
                interior_coords = list(interior.coords[:-1])
                n_interior = len(interior_coords)
                interior_vertex_counts.append(n_interior)

                # Add bottom and top vertices for interior
                for x, y in interior_coords:
                    vertices.append([x, y, building_base_z])
                for x, y in interior_coords:
                    vertices.append([x, y, building_base_z + height])

                # Interior side faces (reversed winding for holes)
                for idx in range(n_interior):
                    next_idx = (idx + 1) % n_interior
                    v1 = vertex_offset + idx
                    v2 = vertex_offset + next_idx
                    v3 = vertex_offset + n_interior + idx
                    v4 = vertex_offset + n_interior + next_idx
                    # Reversed winding order for interior walls
                    faces.append([v1, v3, v2])
                    faces.append([v2, v3, v4])

                vertex_offset += n_interior * 2

            # Triangulate top and bottom faces with holes using trimesh
            try:
                # Use triangulate_polygon to get 2D triangulation (not extruded mesh)
                tri_vertices, tri_faces = trimesh.creation.triangulate_polygon(polygon, engine='earcut')

                # Add bottom face vertices and faces
                bottom_offset = len(vertices)
                for x, y in tri_vertices:
                    vertices.append([x, y, building_base_z])

                # Add bottom faces (reversed winding for proper normals pointing down)
                for face in tri_faces:
                    faces.append([bottom_offset + face[2], bottom_offset + face[1], bottom_offset + face[0]])

                # Add top face vertices and faces
                top_offset = len(vertices)
                for x, y in tri_vertices:
                    vertices.append([x, y, building_base_z + height])

                # Add top faces (normal winding for upward normals)
                for face in tri_faces:
                    faces.append([top_offset + face[0], top_offset + face[1], top_offset + face[2]])

                if polygon.interiors:
                    print(f"  ✓ Successfully triangulated {len(polygon.interiors)} courtyard(s)")
            except Exception as e:
                print(f"  ✗ Triangulation failed: {e}")
                print(f"  → Falling back to simple triangulation (courtyards will be FILLED)")
                # Fallback: simple fan triangulation for exterior only (no holes)
                # Bottom face (reversed winding)
                for idx in range(1, n_exterior - 1):
                    faces.append([0, idx, idx + 1])
                # Top face
                for idx in range(1, n_exterior - 1):
                    faces.append([n_exterior, n_exterior + idx, n_exterior + idx + 1])

            mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces))

            # Fix any mesh issues (normals, degenerate faces, etc.)
            mesh.remove_degenerate_faces()
            mesh.remove_duplicate_faces()
            mesh.fix_normals()

            # Add roof if enabled (only for certain building types)
            if config.building_roofs:
                # Only add pyramid roofs to residential and religious buildings
                building_tags = building.get('tags', {})
                building_type = building_tags.get('building', '').lower()
                amenity = building_tags.get('amenity', '').lower()

                should_add_roof = (
                    building_type in ['house', 'residential', 'apartments', 'detached', 'terrace', 'dormitory'] or
                    building_type in ['church', 'cathedral', 'chapel', 'mosque', 'synagogue', 'temple'] or
                    building_type in ['civic', 'government', 'public', 'courthouse', 'townhall', 'city_hall'] or
                    amenity == 'place_of_worship' or
                    amenity in ['courthouse', 'townhall', 'community_centre']
                )

                # Debug roof decision
                if building_type in ['church', 'cathedral', 'chapel', 'mosque', 'synagogue', 'temple'] or amenity == 'place_of_worship':
                    print(f"ROOF CHECK: Building {i}, type={building_type}, amenity={amenity}, should_add_roof={should_add_roof}, is_building_part={building.get('is_building_part', False)}")

                # CRITICAL: Only add roofs to building:part elements (towers/chimneys), NOT to base buildings
                # This prevents roofs from being applied to the base of multi-part structures like courthouses
                if should_add_roof and building.get('is_building_part', False):
                    print(f"ROOF ADDED: Building {i}, type={building_type}, is_building_part=True")
                    # Roof height is 30% of building height (looks good for pitched roofs)
                    roof_height = height * 0.3
                    roof_top_z = building_base_z + height

                    # Create pyramid roof on top of building
                    roof_mesh = create_pyramid_roof(polygon, roof_top_z, roof_height)

                    if roof_mesh is not None:
                        roof_mesh.remove_degenerate_faces()
                        roof_mesh.fix_normals()

                        # Combine building body and roof into single mesh
                        mesh = trimesh.util.concatenate([mesh, roof_mesh])

            # Add mesh if it has valid geometry
            if len(mesh.vertices) > 0 and len(mesh.faces) > 0:
                meshes.append(mesh)

        except Exception as e:
            print(f"Error creating mesh for building {i}: {e}")
            continue

    print(f"Created {len(meshes)} valid building meshes")
    return meshes


def create_heart_mesh(size_mm=8, height_mm=5):
    """
    Create a simple heart-shaped marker mesh.
    Returns trimesh object.
    """
    # Simple heart approximation using a circle (for MVP)
    # We can make this fancier later
    radius = size_mm / 2
    resolution = 16

    # Create circle profile
    angles = np.linspace(0, 2*np.pi, resolution)
    x = radius * np.cos(angles)
    y = radius * np.sin(angles)

    # Create bottom and top vertices
    bottom_verts = [[x[i], y[i], 0] for i in range(len(angles))]
    top_verts = [[x[i], y[i], height_mm] for i in range(len(angles))]

    vertices = bottom_verts + top_verts

    # Create faces
    faces = []
    n = len(angles)

    # Triangulate bottom face using fan triangulation (reversed for correct normal)
    for i in range(1, n - 1):
        faces.append([0, i + 1, i])

    # Triangulate top face using fan triangulation
    for i in range(1, n - 1):
        faces.append([n, n + i, n + i + 1])

    # Side faces
    for i in range(n):
        next_i = (i + 1) % n
        faces.append([i, next_i, next_i + n])
        faces.append([i, next_i + n, i + n])

    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces))
    return mesh


def create_water_meshes(water_features, center_lat, center_lon, scale_factor=1.0, circle_boundary=None, config=None):
    """Create water meshes with configurable parameters"""
    if config is None:
        config = ModelConfig()

    # DEBUG LOGGING
    with open('/tmp/water_debug.log', 'a') as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"create_water_meshes called\n")
        f.write(f"Input features: {len(water_features)}\n")
        f.write(f"Scale factor: {scale_factor}\n")
        f.write(f"Min area threshold: {config.water_min_area_m2} m²\n")

    meshes = []
    polygons = []  # Store polygons for base plate cutouts
    # Map2model approach: water sits BELOW bed level (negative Z)
    # Base plate has holes where water is, creating first-layer color change effect
    water_base_z = -0.80  # mm - BELOW print bed (map2model approach)
    water_top_z = -0.40   # mm - still BELOW bed level
    water_thickness = water_top_z - water_base_z  # 0.4mm thick

    filtered_count = 0
    for i, feature in enumerate(water_features):
        try:
            footprint_meters = []
            for lat, lon in feature['footprint']:
                x, y = lat_lon_to_meters(lat, lon, center_lat, center_lon)
                footprint_meters.append([x * scale_factor, y * scale_factor])

            if len(footprint_meters) < 3:
                continue

            polygon = Polygon(footprint_meters)
            if not polygon.is_valid or polygon.is_empty:
                continue

            # Filter by minimum area BEFORE clipping (more accurate)
            # Convert mm² to m²: divide by (1000 * scale_factor)²
            area_mm2 = polygon.area
            area_m2 = area_mm2 / (scale_factor * scale_factor)

            # Different thresholds for different water types
            # Rivers are linear features (long but narrow) - use very low threshold
            # Lakes/ponds are area features - use higher threshold
            water_type = feature.get('water_type', 'unknown')
            if water_type in ['river', 'canal', 'stream']:
                min_area_threshold = 10.0  # m² - very low for rivers (long but narrow)
            else:
                min_area_threshold = config.water_min_area_m2  # 100 m² for lakes/ponds

            # DEBUG LOGGING
            with open('/tmp/water_debug.log', 'a') as f:
                f.write(f"Feature {i} ({water_type}): area_mm2={area_mm2:.2f}, area_m2={area_m2:.2f}, threshold={min_area_threshold} m², ")
                if area_m2 < min_area_threshold:
                    f.write(f"FILTERED (< {min_area_threshold})\n")
                else:
                    f.write(f"KEPT (>= {min_area_threshold})\n")

            if area_m2 < min_area_threshold:
                filtered_count += 1
                continue

            # Clip to circular boundary if provided
            if circle_boundary:
                polygon_before = polygon
                polygon = clip_polygon_to_circle(polygon, circle_boundary)
                if polygon is None:
                    # DEBUG LOGGING
                    with open('/tmp/water_debug.log', 'a') as f:
                        f.write(f"  → Feature {i} DISCARDED: circular clipping returned None\n")
                    continue

                # DEBUG LOGGING
                with open('/tmp/water_debug.log', 'a') as f:
                    area_after_mm2 = polygon.area if hasattr(polygon, 'area') else 0
                    area_after_m2 = area_after_mm2 / (scale_factor * scale_factor)
                    f.write(f"  → Feature {i} after clipping: {area_after_m2:.2f} m² (was {area_m2:.2f} m²)\n")

            # Store polygon for base plate cutouts
            polygons.append(polygon)

            # Use extrude_polygon to properly handle both Polygon and MultiPolygon
            # This automatically preserves all pieces when water is split by clipping
            from trimesh.creation import extrude_polygon

            try:
                mesh = extrude_polygon(polygon, height=water_thickness)
                # Position: bottom at water_base_z, top at water_top_z
                mesh.apply_translation([0, 0, water_base_z])
                meshes.append(mesh)
            except Exception as e:
                print(f"Warning: Failed to extrude water polygon: {e}")
                # Fallback to manual triangulation for simple Polygon only
                if polygon.geom_type == 'Polygon':
                    coords = list(polygon.exterior.coords[:-1])
                    n_points = len(coords)

                    if n_points < 3:
                        continue

                    vertices = []
                    # Bottom vertices (at water_base_z)
                    for x, y in coords:
                        vertices.append([x, y, water_base_z])
                    # Top vertices
                    for x, y in coords:
                        vertices.append([x, y, water_top_z])

                    # Create faces
                    faces = []
                    # Bottom face
                    for i in range(1, n_points - 1):
                        faces.append([0, i + 1, i])
                    # Top face
                    for i in range(1, n_points - 1):
                        faces.append([n_points, n_points + i, n_points + i + 1])
                    # Side faces
                    for i in range(n_points):
                        next_i = (i + 1) % n_points
                        faces.append([i, next_i, n_points + i])
                        faces.append([next_i, n_points + next_i, n_points + i])

                    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces))
                    meshes.append(mesh)

            # DEBUG LOGGING
            with open('/tmp/water_debug.log', 'a') as f:
                f.write(f"  → Created mesh {i}: {len(mesh.faces)} faces, {len(mesh.vertices)} vertices\n")

        except Exception as e:
            # DEBUG LOGGING
            with open('/tmp/water_debug.log', 'a') as f:
                f.write(f"  → EXCEPTION creating mesh {i}: {str(e)}\n")
            continue

    print(f"Created {len(meshes)} water meshes (filtered {filtered_count} small waterways, clipped to circular boundary)")

    # DEBUG LOGGING
    with open('/tmp/water_debug.log', 'a') as f:
        f.write(f"Final result: {len(meshes)} meshes, {len(polygons)} polygons, total faces: {sum(len(m.faces) for m in meshes)}\n")

    return meshes, polygons


def create_park_meshes(parks, center_lat, center_lon, scale_factor=1.0, circle_boundary=None, config=None):
    """Create park meshes with configurable parameters"""
    if config is None:
        config = ModelConfig()

    meshes = []
    park_base_z = 0.05  # mm - slightly elevated to prevent vertex merging with base/roads
    park_thickness = config.park_height_mm  # mm - configurable
    park_top_z = park_base_z + park_thickness

    for park in parks:
        try:
            footprint_meters = []
            for lat, lon in park['footprint']:
                x, y = lat_lon_to_meters(lat, lon, center_lat, center_lon)
                footprint_meters.append([x * scale_factor, y * scale_factor])

            if len(footprint_meters) < 3:
                continue

            polygon = Polygon(footprint_meters)
            if not polygon.is_valid or polygon.is_empty:
                continue

            # Clip to circular boundary if provided
            if circle_boundary:
                polygon = clip_polygon_to_circle(polygon, circle_boundary)
                if polygon is None:
                    continue

            # Use extrude_polygon to properly handle polygons with holes (interior greenspaces)
            # This preserves interior holes that represent greenspaces within parks
            from trimesh.creation import extrude_polygon

            try:
                mesh = extrude_polygon(polygon, height=park_thickness)
                # Position: bottom at park_base_z, top at park_top_z
                mesh.apply_translation([0, 0, park_base_z])
            except Exception as e:
                print(f"Warning: Failed to extrude park polygon (may have holes): {e}")
                # Fallback to simple extrusion without holes
                coords = list(polygon.exterior.coords[:-1])
                n_points = len(coords)

                if n_points < 3:
                    continue

                vertices = []
                # Bottom vertices (at park_base_z)
                for x, y in coords:
                    vertices.append([x, y, park_base_z])
                # Top vertices
                for x, y in coords:
                    vertices.append([x, y, park_top_z])

                faces = []
                # Bottom face
                for i in range(1, n_points - 1):
                    faces.append([0, i + 1, i])
                # Top face
                for i in range(1, n_points - 1):
                    faces.append([n_points, n_points + i, n_points + i + 1])
                # Side faces
                for i in range(n_points):
                    next_i = (i + 1) % n_points
                    faces.append([i, next_i, n_points + i])
                    faces.append([next_i, n_points + next_i, n_points + i])

                mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces))
            meshes.append(mesh)
        except Exception as e:
            continue

    print(f"Created {len(meshes)} park meshes (clipped to circular boundary)")
    return meshes


def create_road_meshes(roads, center_lat, center_lon, scale_factor=1.0, circle_boundary=None, config=None):
    """Create thin extruded road meshes with configurable parameters"""
    if config is None:
        config = ModelConfig()

    meshes = []
    road_width = 2.0 * config.road_width_multiplier  # mm, with multiplier
    road_base_z = 0.0 + config.road_depth_mm  # mm - sits on top of base plate at z=0
    road_thickness = config.road_height_mm  # mm - configurable height
    road_top_z = road_base_z + road_thickness

    for road in roads:
        try:
            path_meters = []
            for lat, lon in road['path']:
                x, y = lat_lon_to_meters(lat, lon, center_lat, center_lon)
                path_meters.append([x * scale_factor, y * scale_factor])

            if len(path_meters) < 2:
                continue

            # Clip to circular boundary if provided
            if circle_boundary:
                clipped_segments = clip_linestring_to_circle(path_meters, circle_boundary)
                if not clipped_segments:
                    continue
            else:
                clipped_segments = [path_meters]

            # Create road meshes for each clipped segment
            for path_segment in clipped_segments:
                if len(path_segment) < 2:
                    continue

                # Create a thin box along the path at elevated height
                for i in range(len(path_segment) - 1):
                    p1 = np.array(path_segment[i])
                    p2 = np.array(path_segment[i + 1])

                    # Direction vector
                    direction = p2 - p1
                    length = np.linalg.norm(direction)
                    if length < 0.1:
                        continue

                    direction = direction / length

                    # Perpendicular vector
                    perp = np.array([-direction[1], direction[0]])

                    # Create box vertices
                    half_width = road_width / 2
                    corners = [
                        p1 - perp * half_width,
                        p1 + perp * half_width,
                        p2 + perp * half_width,
                        p2 - perp * half_width,
                    ]

                    vertices = []
                    # Bottom layer (at road_base_z)
                    for x, y in corners:
                        vertices.append([x, y, road_base_z])
                    # Top layer
                    for x, y in corners:
                        vertices.append([x, y, road_top_z])

                    faces = [
                        # Bottom
                        [0, 2, 1], [0, 3, 2],
                        # Top
                        [4, 5, 6], [4, 6, 7],
                        # Sides
                        [0, 1, 5], [0, 5, 4],
                        [1, 2, 6], [1, 6, 5],
                        [2, 3, 7], [2, 7, 6],
                        [3, 0, 4], [3, 4, 7],
                    ]

                    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces))
                    meshes.append(mesh)
        except Exception as e:
            continue

    print(f"Created {len(meshes)} road segment meshes (clipped to circular boundary)")
    return meshes


def create_footpath_meshes(footpaths, center_lat, center_lon, scale_factor=1.0, circle_boundary=None, config=None):
    """Create thin extruded footpath meshes with configurable parameters"""
    if config is None:
        config = ModelConfig()

    meshes = []
    # Convert footpath width from meters to mm at the model scale
    footpath_width_mm = config.footpath_width_m * scale_factor  # Width in mm after scaling
    # Footpaths sit on base (same as roads and parks)
    # Boolean operations will handle overlaps to prevent z-fighting
    footpath_base_z = 0.0  # mm - sits on top of base plate at z=0
    footpath_thickness = config.footpath_height_mm  # mm - configurable height
    footpath_top_z = footpath_base_z + footpath_thickness

    for footpath in footpaths:
        try:
            path_meters = []
            for lat, lon in footpath['path']:
                x, y = lat_lon_to_meters(lat, lon, center_lat, center_lon)
                path_meters.append([x * scale_factor, y * scale_factor])

            if len(path_meters) < 2:
                continue

            # Clip to circular boundary if provided
            if circle_boundary:
                clipped_segments = clip_linestring_to_circle(path_meters, circle_boundary)
                if not clipped_segments:
                    continue
            else:
                clipped_segments = [path_meters]

            # Create footpath meshes for each clipped segment
            for path_segment in clipped_segments:
                if len(path_segment) < 2:
                    continue

                # Create a thin box along the path
                for i in range(len(path_segment) - 1):
                    p1 = np.array(path_segment[i])
                    p2 = np.array(path_segment[i + 1])

                    # Direction vector
                    direction = p2 - p1
                    length = np.linalg.norm(direction)
                    if length < 0.1:
                        continue

                    direction = direction / length

                    # Perpendicular vector
                    perp = np.array([-direction[1], direction[0]])

                    # Create box vertices
                    half_width = footpath_width_mm / 2
                    corners = [
                        p1 - perp * half_width,
                        p1 + perp * half_width,
                        p2 + perp * half_width,
                        p2 - perp * half_width,
                    ]

                    vertices = []
                    # Bottom layer (at footpath_base_z)
                    for x, y in corners:
                        vertices.append([x, y, footpath_base_z])
                    # Top layer
                    for x, y in corners:
                        vertices.append([x, y, footpath_top_z])

                    faces = [
                        # Bottom
                        [0, 2, 1], [0, 3, 2],
                        # Top
                        [4, 5, 6], [4, 6, 7],
                        # Sides
                        [0, 1, 5], [0, 5, 4],
                        [1, 2, 6], [1, 6, 5],
                        [2, 3, 7], [2, 7, 6],
                        [3, 0, 4], [3, 4, 7],
                    ]

                    mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces))
                    meshes.append(mesh)
        except Exception as e:
            continue

    print(f"Created {len(meshes)} footpath segment meshes (clipped to circular boundary)")
    return meshes


def create_base_plate(size_mm=150, thickness_mm=1, water_polygons=None):
    """
    Create a circular base plate with water depressions (map2model approach).

    Structure:
    - Base has TWO layers:
      1. Solid floor everywhere: z=-1.0 to z=-0.80 (provides floor under water)
      2. Walls with water cutouts: z=-0.80 to z=0.0 (water-shaped depressions)
    - Water sits in depressions: z=-0.80 to -0.40
    - Roads/parks sit on top: z=0.0 upward

    Args:
        size_mm: Diameter of base plate
        thickness_mm: Thickness of base (default 1mm)
        water_polygons: List of Shapely polygons for water depressions
    """
    from shapely.geometry import Polygon, Point
    from shapely.ops import unary_union
    import trimesh

    radius = size_mm / 2
    resolution = 64

    # Create circular base polygon
    angles = np.linspace(0, 2*np.pi, resolution, endpoint=False)
    circle_coords = [[radius * np.cos(a), radius * np.sin(a)] for a in angles]
    circle_polygon = Polygon(circle_coords)

    # TWO-LAYER APPROACH (map2model style):
    # Layer 1: Solid floor everywhere (z=-1.0 to z=-0.80)
    # Layer 2: Walls with water cutouts (z=-0.80 to z=0.0)

    try:
        from trimesh.creation import extrude_polygon

        # ===== LAYER 1: SOLID FLOOR (z=-1.0 to z=-0.80) =====
        # This is ALWAYS solid - no water cutouts
        floor_height = 0.20  # mm (from z=-1.0 to z=-0.80)
        floor_mesh = extrude_polygon(circle_polygon, height=floor_height)
        floor_mesh.apply_translation([0, 0, -1.0])  # Bottom at z=-1.0, top at z=-0.80
        print(f"DEBUG: Created solid floor layer: z=-1.0 to z=-0.80")

        # ===== LAYER 2: WALLS WITH WATER CUTOUTS (z=-0.80 to z=0.0) =====
        walls_height = 0.80  # mm (from z=-0.80 to z=0.0)
        walls_polygon = circle_polygon

        # Subtract water polygons from walls layer
        if water_polygons and len(water_polygons) > 0:
            print(f"DEBUG: create_base_plate received {len(water_polygons)} water polygons")
            try:
                # Union all water polygons
                water_union = unary_union(water_polygons)
                print(f"DEBUG: Water union area: {water_union.area:.2f} mm²")

                # Subtract water from walls to create depressions
                walls_before_area = walls_polygon.area
                walls_polygon = walls_polygon.difference(water_union)
                walls_after_area = walls_polygon.area if hasattr(walls_polygon, 'area') else 0

                print(f"DEBUG: Walls area before cutout: {walls_before_area:.2f} mm²")
                print(f"DEBUG: Walls area after cutout: {walls_after_area:.2f} mm²")
                print(f"DEBUG: Walls polygon type after difference: {walls_polygon.geom_type}")

                # Handle MultiPolygon (if water splits walls into pieces)
                # IMPORTANT: Keep ALL pieces, not just the largest!
                if walls_polygon.geom_type == 'MultiPolygon':
                    print(f"DEBUG: Walls split into {len(walls_polygon.geoms)} pieces - KEEPING ALL")
            except Exception as e:
                print(f"WARNING: Failed to cut water from walls: {e}")
                import traceback
                traceback.print_exc()
                # Fall back to solid walls if boolean operation fails
                pass
        else:
            print(f"DEBUG: No water polygons provided - walls will be solid")

        # Extrude walls layer
        if walls_polygon.geom_type == 'MultiPolygon':
            # Extrude each piece separately and combine
            walls_meshes = []
            for poly in walls_polygon.geoms:
                piece_mesh = extrude_polygon(poly, height=walls_height)
                piece_mesh.apply_translation([0, 0, -0.80])  # Bottom at z=-0.80, top at z=0.0
                walls_meshes.append(piece_mesh)
            walls_mesh = trimesh.util.concatenate(walls_meshes)
            print(f"DEBUG: Combined {len(walls_meshes)} wall pieces into single mesh")
        else:
            # Single polygon - extrude normally
            walls_mesh = extrude_polygon(walls_polygon, height=walls_height)
            walls_mesh.apply_translation([0, 0, -0.80])  # Bottom at z=-0.80, top at z=0.0

        print(f"DEBUG: Created walls layer with water depressions: z=-0.80 to z=0.0")

        # ===== COMBINE BOTH LAYERS =====
        mesh = trimesh.util.concatenate([floor_mesh, walls_mesh])
        print(f"DEBUG: Combined floor and walls into single base plate mesh")

    except Exception as e:
        print(f"Warning: Failed to extrude base with holes: {e}")
        # Fallback: create simple solid base
        vertices = []
        faces = []

        # Bottom vertices (at z=-thickness_mm, below bed)
        for x, y in circle_coords:
            vertices.append([x, y, -thickness_mm])

        # Top vertices (at z=0, bed level)
        for x, y in circle_coords:
            vertices.append([x, y, 0])

        # Bottom face (triangulated from center)
        bottom_center = len(vertices)
        vertices.append([0, 0, -thickness_mm])
        for i in range(resolution):
            next_i = (i + 1) % resolution
            faces.append([bottom_center, i, next_i])

        # Top face
        top_center = len(vertices)
        vertices.append([0, 0, 0])
        for i in range(resolution):
            next_i = (i + 1) % resolution
            faces.append([top_center, resolution + next_i, resolution + i])

        # Sides
        for i in range(resolution):
            next_i = (i + 1) % resolution
            faces.append([i, next_i, resolution + next_i])
            faces.append([i, resolution + next_i, resolution + i])

        mesh = trimesh.Trimesh(vertices=np.array(vertices), faces=np.array(faces))

    return mesh


def create_border_ring(size_mm=150, ring_width_mm=5):
    """
    Create a circular border ring around the map (map2model component FFFFFF_02).

    Structure:
    - Annulus (donut) shape: outer circle with inner hole
    - Extends from z=-1.0 to z=0.40 (1.4mm thick)
    - Serves as raised border for the map

    Args:
        size_mm: Diameter of outer circle (same as base plate)
        ring_width_mm: Width of the ring (default 5mm - increased from 3mm for visibility)

    Returns:
        Trimesh object of the border ring
    """
    from shapely.geometry import Polygon, Point
    from trimesh.creation import extrude_polygon
    import trimesh

    outer_radius = size_mm / 2
    inner_radius = outer_radius - ring_width_mm
    resolution = 64

    print(f"DEBUG: Creating border ring with outer_r={outer_radius:.2f}mm, inner_r={inner_radius:.2f}mm, width={ring_width_mm}mm")

    # Create outer circle
    angles = np.linspace(0, 2*np.pi, resolution, endpoint=False)
    outer_coords = [[outer_radius * np.cos(a), outer_radius * np.sin(a)] for a in angles]
    outer_polygon = Polygon(outer_coords)

    # Create inner circle (hole)
    inner_coords = [[inner_radius * np.cos(a), inner_radius * np.sin(a)] for a in angles]
    inner_polygon = Polygon(inner_coords)

    print(f"DEBUG: Outer polygon valid={outer_polygon.is_valid}, area={outer_polygon.area:.2f}")
    print(f"DEBUG: Inner polygon valid={inner_polygon.is_valid}, area={inner_polygon.area:.2f}")

    # Subtract inner from outer to create ring (annulus)
    ring_polygon = outer_polygon.difference(inner_polygon)

    print(f"DEBUG: Ring polygon type={ring_polygon.geom_type}, valid={ring_polygon.is_valid}, area={ring_polygon.area:.2f}")

    # Extrude ring from z=-1.0 to z=0.40 (1.4mm total height)
    ring_height = 1.4  # mm
    ring_mesh = extrude_polygon(ring_polygon, height=ring_height)

    # Position: bottom at z=-1.0, top at z=0.40
    ring_mesh.apply_translation([0, 0, -1.0])

    print(f"DEBUG: Border ring mesh created: {len(ring_mesh.vertices)} vertices, {len(ring_mesh.faces)} faces")
    print(f"DEBUG: Border ring Z-range: {ring_mesh.vertices[:, 2].min():.3f} to {ring_mesh.vertices[:, 2].max():.3f}")

    return ring_mesh


def hex_to_rgb(hex_color):
    """Convert hex color string to RGB tuple (0-255 range)"""
    hex_color = hex_color.lstrip('#')
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def apply_vertex_colors(meshes, hex_color):
    """
    Apply a single color to all vertices of a list of meshes.
    Returns list of meshes with vertex colors assigned.
    """
    if not meshes:
        return []

    r, g, b = hex_to_rgb(hex_color)
    rgba = np.array([r, g, b, 255], dtype=np.uint8)

    colored_meshes = []
    for mesh in meshes:
        # Create color array for all vertices
        vertex_colors = np.tile(rgba, (len(mesh.vertices), 1))
        mesh.visual.vertex_colors = vertex_colors
        colored_meshes.append(mesh)

    return colored_meshes


# ============================================================================
# MAIN GENERATION FUNCTION
# ============================================================================

def generate_stl(lat, lon, radius_meters=300, config=None, export_format='stl'):
    """
    Main function to generate STL files with customizable parameters.
    Returns dictionary of generated filenames.
    """
    if config is None:
        config = ModelConfig()  # Use defaults if no config provided

    print(f"\n{'='*60}")
    print(f"Generating STL for location: ({lat}, {lon})")
    print(f"Config: buildings={config.building_color}, roads={config.road_color}")
    print(f"{'='*60}\n")

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    # Calculate scale factor
    max_extent = radius_meters * 2
    scale_factor = BASE_SIZE_MM / max_extent
    print(f"Scale factor: {scale_factor:.4f} (fitting {max_extent}m into {BASE_SIZE_MM}mm)")

    # Create circular boundaries:
    # - Outer boundary (75mm radius) for base plate
    # - Inner boundary (70mm radius) for clipping features to avoid border ring intersection
    circle_boundary_outer = create_circular_boundary(radius_meters, scale_factor)

    # Calculate inner radius: reduce by 5mm (border ring width) in scaled space
    border_width_mm = 5.0  # mm
    inner_radius_reduction_meters = border_width_mm / scale_factor
    inner_radius_meters = radius_meters - inner_radius_reduction_meters
    circle_boundary = create_circular_boundary(inner_radius_meters, scale_factor)

    print(f"Created circular boundaries:")
    print(f"  Outer (base): {radius_meters}m -> {radius_meters * scale_factor:.2f}mm radius")
    print(f"  Inner (features): {inner_radius_meters}m -> {inner_radius_meters * scale_factor:.2f}mm radius")
    print(f"  Border ring width: {border_width_mm}mm")

    # Query all feature types (with config-based type filtering)
    buildings = query_osm_buildings(lat, lon, radius_meters)
    water_features = query_osm_water(lat, lon, radius_meters, config)
    parks = query_osm_parks(lat, lon, radius_meters)
    roads = query_osm_roads(lat, lon, radius_meters, config)
    footpaths = query_osm_footpaths(lat, lon, radius_meters) if config.footpath_enabled else []

    if not buildings:
        raise ValueError("No buildings found in this area!")

    # Create all mesh types FIRST (need water polygons before creating base)
    # IMPORTANT: Water uses OUTER boundary (75mm) since it's below border ring (z=-0.80 to -0.40)
    # Other features use INNER boundary (70mm) to avoid border ring collision (z=0.0+)
    building_meshes = create_building_meshes(buildings, lat, lon, scale_factor, circle_boundary, config)
    water_meshes, water_polygons = create_water_meshes(water_features, lat, lon, scale_factor, circle_boundary_outer, config)
    park_meshes = create_park_meshes(parks, lat, lon, scale_factor, circle_boundary, config)
    road_meshes = create_road_meshes(roads, lat, lon, scale_factor, circle_boundary, config)
    footpath_meshes = create_footpath_meshes(footpaths, lat, lon, scale_factor, circle_boundary, config) if config.footpath_enabled else []

    # Apply boolean operations to handle overlapping features (prevents z-fighting)
    # Footpaths "cut through" roads and parks where they intersect
    if footpath_meshes:
        print(f"\nResolving feature overlaps with boolean operations...")

        # DEBUG LOGGING
        with open('/tmp/boolean_debug.log', 'a') as f:
            f.write(f"\n{'='*60}\n")
            f.write(f"Boolean operations starting\n")
            f.write(f"Footpath meshes: {len(footpath_meshes)}\n")
            f.write(f"Road meshes: {len(road_meshes)}\n")
            f.write(f"Park meshes: {len(park_meshes)}\n")

        # Combine footpaths into single mesh for boolean operations
        footpath_combined = trimesh.util.concatenate(footpath_meshes)

        # Subtract footpaths from roads (where they overlap)
        if road_meshes:
            try:
                road_combined = trimesh.util.concatenate(road_meshes)
                print(f"  • Subtracting footpaths from roads ({len(road_combined.faces)} road faces, {len(footpath_combined.faces)} footpath faces)...")

                # DEBUG LOGGING
                with open('/tmp/boolean_debug.log', 'a') as f:
                    f.write(f"Attempting road boolean: {len(road_combined.faces)} road faces, {len(footpath_combined.faces)} footpath faces\n")

                road_subtracted = trimesh.boolean.difference([road_combined, footpath_combined], engine='scad')

                # DEBUG LOGGING
                with open('/tmp/boolean_debug.log', 'a') as f:
                    f.write(f"Road boolean result: {type(road_subtracted)}, faces={len(road_subtracted.faces) if road_subtracted else 0}\n")

                # Replace road_meshes with the result
                if road_subtracted is not None and len(road_subtracted.faces) > 0:
                    road_meshes = [road_subtracted]
                    print(f"    ✓ Roads updated: {len(road_subtracted.faces)} faces (boolean operation successful)")
                else:
                    print(f"    ⚠ Boolean operation returned empty result, keeping original roads")
            except Exception as e:
                print(f"    ⚠ Boolean operation failed for roads: {e}")
                print(f"    → Keeping original {len(road_meshes)} road meshes")

                # DEBUG LOGGING
                with open('/tmp/boolean_debug.log', 'a') as f:
                    f.write(f"Road boolean EXCEPTION: {str(e)}\n")
                    import traceback
                    f.write(traceback.format_exc())

                # Keep original road meshes on failure

        # Subtract footpaths from parks (where they overlap)
        if park_meshes:
            try:
                park_combined = trimesh.util.concatenate(park_meshes)
                print(f"  • Subtracting footpaths from parks ({len(park_combined.faces)} park faces, {len(footpath_combined.faces)} footpath faces)...")
                park_subtracted = trimesh.boolean.difference([park_combined, footpath_combined], engine='scad')
                # Replace park_meshes with the result
                if park_subtracted is not None and len(park_subtracted.faces) > 0:
                    park_meshes = [park_subtracted]
                    print(f"    ✓ Parks updated: {len(park_subtracted.faces)} faces (boolean operation successful)")
                else:
                    print(f"    ⚠ Boolean operation returned empty result, keeping original parks")
            except Exception as e:
                print(f"    ⚠ Boolean operation failed for parks: {e}")
                print(f"    → Keeping original {len(park_meshes)} park meshes")
                # Keep original park meshes on failure

    # Create base plate with water cutouts (z=-1.0 to z=0.0, matching map2model)
    # Base stops at inner edge of border ring (70mm radius) to avoid overlap
    BASE_INNER_SIZE_MM = BASE_SIZE_MM - 10  # 140mm diameter (70mm radius)
    base = create_base_plate(BASE_INNER_SIZE_MM, 1, water_polygons=water_polygons)

    # Create border ring (z=-1.0 to z=0.40, matching map2model component FFFFFF_02)
    # Border ring wraps around base: outer 75mm, inner 70mm
    border_ring = create_border_ring(BASE_SIZE_MM, ring_width_mm=5)

    # Center offset (will be applied to all)
    center_offset = None

    if not building_meshes:
        raise ValueError("Failed to create building meshes!")

    # Apply vertex colors to each mesh type (for 3MF export)
    # LAYER ORDER (bottom to top): Water → Base → Parks → Roads → Footpaths → Buildings

    # Store the face count for each feature type (for viewer to apply materials)
    feature_ranges = {
        'water_faces': 0,
        'base_and_heart_faces': 0,
        'park_faces': 0,
        'road_faces': 0,
        'footpath_faces': 0,
        'building_faces': 0
    }

    # Start with empty list
    all_meshes = []

    # DEBUG LOGGING
    with open('/tmp/export_debug.log', 'a') as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"Layer assembly starting\n")
        f.write(f"water_meshes: {len(water_meshes)} meshes, {sum(len(m.faces) for m in water_meshes) if water_meshes else 0} total faces\n")

    # Layer 1: Water (lowest - at z=0)
    if water_meshes:
        water_colored = apply_vertex_colors(water_meshes, config.water_color)
        all_meshes.extend(water_colored)
        feature_ranges['water_faces'] = sum(len(m.faces) for m in water_meshes)
        print(f"✓ Water: {len(water_meshes)} features")

        # DEBUG LOGGING
        with open('/tmp/export_debug.log', 'a') as f:
            f.write(f"Added {len(water_colored)} colored water meshes to all_meshes\n")
    else:
        # DEBUG LOGGING
        with open('/tmp/export_debug.log', 'a') as f:
            f.write(f"NO WATER MESHES - water_meshes is empty or None\n")

    # Layer 2 & 2.5: Base plate and border ring (both white, same color component)
    # IMPORTANT: Color them together so they get the same color ID in 3MF export
    base_and_border_colored = apply_vertex_colors([base, border_ring], '#FFFFFF')  # White (map2model)
    all_meshes.extend(base_and_border_colored)
    feature_ranges['base_and_heart_faces'] = len(base.faces) + len(border_ring.faces)
    print(f"✓ Base + Border ring: {len(base.faces)} + {len(border_ring.faces)} faces (same color)")

    # Layer 3: Parks (green spaces)
    if park_meshes:
        parks_colored = apply_vertex_colors(park_meshes, config.park_color)
        all_meshes.extend(parks_colored)
        feature_ranges['park_faces'] = sum(len(m.faces) for m in park_meshes)
        print(f"✓ Parks: {len(park_meshes)} features")

    # Layer 4: Roads
    if road_meshes:
        roads_colored = apply_vertex_colors(road_meshes, config.road_color)
        all_meshes.extend(roads_colored)
        feature_ranges['road_faces'] = sum(len(m.faces) for m in road_meshes)
        print(f"✓ Roads: {len(road_meshes)} features")

    # Layer 5: Footpaths
    if footpath_meshes:
        footpaths_colored = apply_vertex_colors(footpath_meshes, config.footpath_color)
        all_meshes.extend(footpaths_colored)
        feature_ranges['footpath_faces'] = sum(len(m.faces) for m in footpath_meshes)
        print(f"✓ Footpaths: {len(footpath_meshes)} features")

    # Layer 6: Buildings (highest)
    buildings_colored = apply_vertex_colors(building_meshes, config.building_color)
    all_meshes.extend(buildings_colored)
    feature_ranges['building_faces'] = sum(len(m.faces) for m in building_meshes)

    # Combine all meshes in correct order
    if not all_meshes:
        raise ValueError("No meshes to combine!")
    combined = trimesh.util.concatenate(all_meshes)

    # Center the combined model in X and Y only (preserve Z for base frame structure)
    center_offset = combined.bounds.mean(axis=0)
    center_offset[2] = 0  # Don't center Z-axis (preserve base frame heights)
    combined.vertices -= center_offset

    # Always export both STL (for preview) and 3MF (for download)
    stl_filename = f'map_{timestamp}.stl'
    mf3_filename = f'map_{timestamp}.3mf'
    stl_filepath = os.path.join(OUTPUT_DIR, stl_filename)
    mf3_filepath = os.path.join(OUTPUT_DIR, mf3_filename)

    # Export STL (for 3D preview on website)
    print(f"Exporting STL for preview...")
    combined.export(stl_filepath, file_type='stl')

    # Export 3MF with full color support (for download)
    print(f"Exporting 3MF with vertex colors...")
    combined.export(mf3_filepath, file_type='3mf')

    # Export PLY with full color support (ACTUALLY works with colors!)
    ply_filename = f'map_{timestamp}.ply'
    ply_filepath = os.path.join(OUTPUT_DIR, ply_filename)
    print(f"Exporting PLY with vertex colors...")
    combined.export(ply_filepath, file_type='ply')

    # Always generate split export files for multi-color printing
    import zipfile
    zip_filename = f'map_{timestamp}_split.zip'
    zip_filepath = os.path.join(OUTPUT_DIR, zip_filename)

    print(f"\nExporting split components for multi-color printing...")

    with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
        # Export each feature type separately
        components = [
            ('water', water_meshes),
            ('base', [base]),
            ('border_ring', [border_ring]),  # NEW: 8th component (FFFFFF_02)
            ('parks', park_meshes),
            ('roads', road_meshes),
            ('footpaths', footpath_meshes),
            ('buildings', building_meshes)
        ]

        for component_name, meshes in components:
            # DEBUG LOGGING
            with open('/tmp/export_debug.log', 'a') as f:
                f.write(f"Exporting {component_name}: {len(meshes) if meshes else 0} meshes\n")

            if meshes:
                # Combine meshes for this component
                component_combined = trimesh.util.concatenate(meshes)
                # Center it the same way
                component_combined.vertices -= center_offset

                # DEBUG LOGGING
                with open('/tmp/export_debug.log', 'a') as f:
                    f.write(f"  Combined: {len(component_combined.faces)} faces, {len(component_combined.vertices)} vertices\n")

                # Export to STL
                component_filename = f'map_{timestamp}_{component_name}.stl'
                component_filepath = os.path.join(OUTPUT_DIR, component_filename)
                component_combined.export(component_filepath, file_type='stl')

                # Add to zip
                zipf.write(component_filepath, arcname=component_filename)
                print(f"  ✓ {component_name}: {len(component_combined.faces)} faces")

                # Clean up individual file (keep only zip)
                os.remove(component_filepath)
            else:
                # DEBUG LOGGING
                with open('/tmp/export_debug.log', 'a') as f:
                    f.write(f"  SKIPPED - meshes is empty\n")

    print(f"✓ Split export complete: {zip_filename}")

    print(f"\n{'='*60}")
    print(f"Generated files:")
    print(f"  STL (preview): {stl_filename}")
    print(f"  3MF (download): {mf3_filename}")
    print(f"  PLY (full color): {ply_filename}")
    print(f"  Split ZIP (multi-color): {zip_filename}")
    print(f"Total faces: {len(combined.faces)}")
    print(f"Has vertex colors: {hasattr(combined.visual, 'vertex_colors')}")
    print(f"Feature breakdown: {feature_ranges}")
    print(f"{'='*60}\n")

    return {
        'stl_filename': stl_filename,
        '3mf_filename': mf3_filename,
        'ply_filename': ply_filename,
        'split_zip': zip_filename,
        'feature_ranges': feature_ranges
    }


# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route('/')
def index():
    """Serve the main page"""
    return render_template('index.html')


@app.route('/generate', methods=['POST'])
def generate():
    """Handle STL generation request"""
    try:
        data = request.json
        lat = float(data.get('lat'))
        lon = float(data.get('lon'))
        radius = int(data.get('radius', 300))

        # Extract config parameters (defaults to map2model values)
        config_dict = data.get('config', {})
        config = ModelConfig(config_dict)

        # Generate combined file with configuration (always generates both STL and 3MF)
        result = generate_stl(lat, lon, radius, config)

        # Return success with filenames (STL, 3MF, PLY, Split ZIP), feature ranges, and colors
        return jsonify({
            'success': True,
            'filename': result['stl_filename'],  # For 3D preview
            '3mf_filename': result['3mf_filename'],  # For download
            'ply_filename': result['ply_filename'],  # For download (full color)
            'split_zip': result['split_zip'],  # For multi-color printing
            'feature_ranges': result['feature_ranges'],
            'colors': {
                'building': config.building_color,
                'road': config.road_color,
                'water': config.water_color,
                'park': config.park_color,
                'footpath': config.footpath_color
            },
            'message': 'Map generated successfully!'
        })

    except Exception as e:
        print(f"Error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 400


@app.route('/download/<filename>')
def download(filename):
    """Serve generated STL file"""
    filepath = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(filepath):
        return send_file(filepath, as_attachment=True)
    else:
        return "File not found", 404


if __name__ == '__main__':
    print("\n" + "="*60)
    print("Valentine's Day Map Generator - MVP")
    print("="*60)
    print(f"Server starting at http://localhost:5000")
    print("="*60 + "\n")

    app.run(debug=False, host='0.0.0.0', port=5000)
