# Plan to Match Map2Model Quality

**Date:** 2026-01-06
**Analysis Source:** Map2Model reference files from split STL + 3MF

---

## KEY FINDINGS FROM MAP2MODEL

### 1. Heights (CRITICAL - Currently Wrong!)

**Map2Model's Proven Heights:**
```
Water:     -1.0mm to -0.6mm  (0.4mm thick, recessed)
Base:      -1.0mm to  0.0mm  (1.0mm thick base plate)
Parks:      0.0mm to  0.4mm  (0.4mm high - SUBTLE)
Roads:      0.0mm to  0.6mm  (0.6mm high - TALLER than parks!)
Buildings:  0.0mm to 57.6mm  (variable real-world heights)
```

**Our Current Settings (TOO TALL!):**
```
Water:     -0.5mm           ❌ Should be -1.0mm
Parks:      1.0mm           ❌ Should be  0.4mm
Roads:      1.0mm           ❌ Should be  0.6mm
```

**Visual Hierarchy Priority:**
1. Water (recessed - creates depth)
2. Parks (subtle raised green)
3. Roads (MORE prominent than parks - key insight!)
4. Buildings (tallest)

---

### 2. Colors (Printability Optimized)

**Map2Model's Colors:**
```
Base:      #FFFFFF  (White - not gray!)
Parks:     #0FE300  (Bright lime green - more vibrant)
Roads:     #262626  (Dark gray - NOT BLACK! More printable)
Buildings: #B8B8B8  (Light gray - lighter than ours)
```

**Our Current Colors:**
```
Base:      #C7C7C8  ❌ Should be #FFFFFF
Parks:     #27B718  ❌ Should be #0FE300 (brighter)
Roads:     #000000  ❌ Should be #262626 (dark gray, not black)
Buildings: #97979A  ❌ Should be #B8B8B8 (lighter)
```

**Why These Colors:**
- **White base** = clean, professional look
- **#262626 roads** = Dark gray prints better than pure black (no stringing)
- **#0FE300 parks** = Vibrant, pops visually
- **#B8B8B8 buildings** = Light enough to contrast with roads

---

### 3. Geometry Quality

**Map2Model Statistics:**
```
Roads file:      1.8MB, 19,178 vertices  (VERY detailed!)
Buildings file:  479KB,  4,641 vertices
Parks files:     141KB total (split into 3 files)
Base files:      51KB total (2 components)
```

**Our Current Output:**
- Need to compare (TODO: generate test and measure)

**Potential Issues:**
- Are our roads detailed enough?
- Are we missing small buildings?
- Polygon simplification tolerance too aggressive?

---

### 4. File Structure

**Map2Model 3MF Organization:**
- 7 separate objects (objectid 0-6)
- Each object = one color/material
- Matches the 7 split STL files
- Proper material assignments in 3MF

**Our Current Output:**
- Single combined mesh with vertex colors
- Works for 3MF/PLY but loses modularity

---

## IMPLEMENTATION PLAN

### Phase 1: Fix Heights & Colors (CRITICAL - Do First)

**Priority: URGENT**

1. **Update ModelConfig defaults in `app.py`:**
   ```python
   # Water
   self.water_color = "#1479C6"  # Keep (close to theirs)
   self.water_height_mm = 0.40   # NOT changed (thickness)

   # Parks
   self.park_color = "#0FE300"   # Change from #27B718
   self.park_height_mm = 0.40    # Change from 1.0

   # Roads
   self.road_color = "#262626"   # Change from #000000 (black)
   self.road_height_mm = 0.60    # Change from 1.0

   # Buildings
   self.building_color = "#B8B8B8"  # Change from #97979A

   # Base (need to add this)
   base_color = "#FFFFFF"  # Change from #C7C7C8
   ```

2. **Update water depth in `create_water_meshes()`:**
   ```python
   water_base_z = -1.0  # Change from -0.5
   ```

3. **Update base color in `generate_stl()`:**
   ```python
   base_colored = apply_vertex_colors([base], '#FFFFFF')  # Change from #C7C7C8
   ```

**Testing:**
- Generate same location as map2model reference
- Compare Z-levels, colors, visual appearance
- Validate printability

---

### Phase 2: Geometry Quality Improvements

1. **Road Detail:**
   - Check if our road width is correct
   - Verify road geometry has enough vertices
   - Compare file size to map2model (should be ~1.8MB for roads alone)

2. **Building Detection:**
   - Ensure we're not filtering out small buildings
   - Check `building_min_area_m2` threshold
   - Verify polygon simplification isn't too aggressive

3. **Parks:**
   - Map2model splits parks into 3 files (why?)
   - Check if we need to separate park types
   - Compare park polygon detail

4. **Simplification Tolerance:**
   ```python
   # Current in create_building_meshes():
   simplify_tolerance = 0.05 if polygon.interiors else 0.1

   # May need adjustment based on testing
   ```

---

### Phase 3: Pin Marker Implementation (NEW FEATURE)

**Design Decision:**
- If pin on building → Make that building RED (#FF0000)
- If pin NOT on building → Place raised RED marker at coordinates

**Implementation:**
1. Add `pin_lat`, `pin_lon` parameters to `generate_stl()`
2. Point-in-polygon check against building footprints
3. If hit: flag building with red color override
4. If miss: create marker geometry at pin location

**Marker Options:**
- Small cylinder (2-3mm diameter, 2mm tall)
- Heart shape (for romantic theme)
- Simple cone

---

### Phase 4: Export Format Optimization

**Current:**
- STL (for preview)
- 3MF (single mesh with vertex colors)
- PLY (full color support)

**Map2Model Approach:**
- Split STLs by feature type
- 3MF with 7 separate objects

**Recommendation:**
- Keep current unified approach (simpler for MVP)
- Add option to export split STLs if needed later

---

## VALIDATION CHECKLIST

After implementing Phase 1:
- [ ] Water at -1.0mm (not -0.5mm)
- [ ] Parks at 0.4mm (not 1.0mm)
- [ ] Roads at 0.6mm (not 1.0mm)
- [ ] Base color #FFFFFF (white)
- [ ] Parks color #0FE300 (bright green)
- [ ] Roads color #262626 (dark gray)
- [ ] Buildings color #B8B8B8 (light gray)
- [ ] Generate test model and compare visually
- [ ] Check Z-levels match map2model histogram
- [ ] Verify colors match in slicer

---

## SUCCESS CRITERIA

**Must Match:**
1. ✅ Z-level structure (water/parks/roads/buildings)
2. ✅ Color palette (white/green/gray scheme)
3. ✅ Visual hierarchy (roads more prominent than parks)

**Should Match:**
1. Geometry detail level (file sizes similar)
2. Building count (not missing small structures)
3. Road coverage and detail

**Nice to Have:**
1. Split export option
2. Exact polygon counts
3. Identical file sizes

---

## NEXT STEPS

1. **IMMEDIATE:** Fix heights and colors (Phase 1)
2. **TEST:** Generate Pittsburgh test location
3. **COMPARE:** Side-by-side with map2model reference
4. **ITERATE:** Adjust based on visual comparison
5. **IMPLEMENT:** Pin marker feature (Phase 3)
6. **OPTIMIZE:** Geometry quality if needed (Phase 2)

---

## TECHNICAL DEBT / FUTURE IMPROVEMENTS

- Add water color variation (ocean vs lake vs river)
- Terrain elevation data (not just buildings)
- Better roof geometry for landmark buildings
- Fuzzy boundary clipping (smooth edges)
- Performance optimization for large areas
- Caching OSM queries
