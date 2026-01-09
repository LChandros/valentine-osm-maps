# Green Space Issue - Root Cause Found & Fixed

## Problem Summary

**You reported:** "We are STILL missing most of the green space that map2model has"

**Analysis revealed:** We have the **same number** of parks (80 vs 79) but **missing ALL large parks**!

## The Smoking Gun 🔍

```
Map2model park distribution:
  - 7 large parks (≥500mm²)
  - Largest: 2,456mm²
  - Medium parks: 19
  - Small/tiny parks: 53

Our park distribution (BEFORE fix):
  - 0 large parks ❌
  - Largest: only 257mm²
  - Medium parks: 11
  - Small/tiny parks: 69
```

**We were missing ALL 7 large parks that provide most of the visible green coverage!**

## Root Cause

**Circular clipping was too aggressive:**

1. Query radius: 400m
2. Scaled to 75mm radius circle (150mm diameter)
3. Large parks are ~50mm wide
4. Any large park near the edge gets clipped heavily or removed entirely

**Visual:**
```
Circle radius: 75mm
Large park width: ~50mm
→ Parks near edges get chopped or eliminated
```

## The Fix Applied ✅

**File:** `app.py` line 1171-1177

**Changed:** Disabled circular clipping for parks

```python
# BEFORE (parks were clipped):
if circle_boundary:
    polygon = clip_polygon_to_circle(polygon, circle_boundary)
    if polygon is None:
        continue

# AFTER (parks can extend beyond circle):
# NOTE: Disabled for parks to preserve large parks near edges
# Parks can extend beyond the circle boundary for better coverage
#if circle_boundary:
#    polygon = clip_polygon_to_circle(polygon, circle_boundary)
#    if polygon is None:
#        continue
```

**Result:** Parks can now extend beyond the circular boundary, preserving large parks

## Expected Results

When you generate a new map, you should see:

✅ **Large parks preserved** - 6-8 large parks (≥500mm²)
✅ **Better green coverage** - Visually matches map2model
✅ **Same park count** - Still ~80-88 parks total
✅ **More visible green space** - Large parks provide most of the visual impact

## Testing Note

OSM API is currently rate-limited (504 timeouts), so immediate testing may fail with "Created 0 park meshes". Wait a few minutes and try again.

## Alternative Solutions (if needed)

If parks extending beyond the circle looks weird:

### Option A: Larger query radius
```python
# In generate_stl(), change:
parks = query_osm_parks(lat, lon, radius_meters + 100)  # Query 500m instead of 400m
# Then clip to 400m circle - gives parks buffer zone
```

### Option B: Add more landuse types
```python
# In query_osm_parks(), add:
way["leisure"="garden"]
way["leisure"="recreation_ground"]
way["natural"="wood"]
way["landuse"="village_green"]
```

## Files

- **Checkpoint:** `app.py.checkpoint-20260106-183303`
- **Fix applied:** `app.py` (current)
- **Docs:** `CHECKPOINT-20260106-182900.md`

## Rollback if Needed

```bash
cp app.py.checkpoint-20260106-183303 app.py
pkill -f "python app.py"
source venv/bin/activate && nohup python app.py > server.log 2>&1 &
```
