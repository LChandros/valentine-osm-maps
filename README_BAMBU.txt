╔═══════════════════════════════════════════════════════════════════════════╗
║                 VALENTINE'S DAY MAP - BAMBU STUDIO GUIDE                  ║
║                      Multi-Color 3D Printing Instructions                  ║
╚═══════════════════════════════════════════════════════════════════════════╝

📦 WHAT'S IN THIS PACKAGE
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  stl/                      - Individual component STL files
  map_multicolor.3mf        - Single-file import (separate objects)
  color_mapping.json        - Color reference guide
  README_BAMBU.txt          - This file


🎨 RECOMMENDED COLORS (7 filaments needed)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Component      Color Name          Hex Code   AMS Slot
  ─────────────  ──────────────────  ─────────  ────────
  1. Water       Light Blue          #3399FF    Slot 1
  2. Base        White               #FFFFFF    Slot 2
  3. Border Ring White (same)        #FFFFFF    Slot 2 (shared)
  4. Parks       Bright Green        #0FE300    Slot 3
  5. Roads       Dark Gray           #262626    Slot 4
  6. Footpaths   Dark Gray (same)    #262626    Slot 4 (shared)
  7. Buildings   Light Gray          #B8B8B8    Slot 5

  Total unique colors: 5 (Water, White, Green, Dark Gray, Light Gray)


🚀 QUICK START - OPTION A: Single File Import (Recommended)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Open Bambu Studio

  2. Drag "map_multicolor.3mf" onto the build plate

  3. Check object list (left sidebar) - you should see 7 separate objects:
     - water
     - base
     - border_ring
     - parks
     - roads
     - footpaths (if enabled)
     - buildings

  4. Assign colors to each object:
     - Right-click object → Select Filament
     - OR: Select object and press 1-9 for AMS slots

  5. Verify AMS slot mapping matches your filaments

  6. Slice and print!


🔧 OPTION B: Import Individual STL Files
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. Open Bambu Studio

  2. Drag ALL files from stl/ folder onto build plate

  3. When prompted "Load these files as a single object?":
     - Click "Yes" to load as one object with multiple parts

  4. Assign colors to parts (same as Option A step 4)


💡 TIPS & TRICKS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ✓ Base & Border Ring can share WHITE filament (same slot)

  ✓ Roads & Footpaths can share DARK GRAY filament (same slot)

  ✓ Total AMS slots needed: 5 (not 7!)

  ✓ Recommended print settings:
    - Layer height: 0.2mm
    - Infill: 15%
    - Supports: None needed (designed support-free)
    - Brim: Optional (helps with adhesion)

  ✓ Water is recessed (0.4mm below base) - prints beautifully!

  ✓ Buildings have vertical exaggeration for dramatic effect


⚠️  TROUBLESHOOTING
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Problem: "The 3mf is not from Bambu Lab, load geometry data only"
  Solution: This is normal! Bambu Studio loads the geometry correctly.
            The objects should still be separate - check the object list.

  Problem: Objects imported as single combined mesh
  Solution: Use Option B (individual STL files) and select "Yes" when
            prompted to load as single object with multiple parts.

  Problem: Colors don't match AMS slots
  Solution: Click filament number in left sidebar, select correct AMS slot.
            You can remap colors without re-importing!

  Problem: Parts are missing
  Solution: Some features (like footpaths) may be disabled. Check which
            STL files are in your package - not all maps have all features.


📏 MODEL SPECIFICATIONS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Dimensions:      ~150mm diameter circular base
  Height:          Variable (depends on buildings in your area)
  Base thickness:  1.0mm floor + 0.8mm walls
  Border ring:     5mm wide ring at edge
  Scale:           Real OpenStreetMap data


🔗 MORE INFORMATION
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  Project: Valentine's Day Map Generator
  GitHub:  https://github.com/LChandros/valentine-osm-maps

  Data source: OpenStreetMap (© OpenStreetMap contributors)
  License: ODbL (https://opendatacommons.org/licenses/odbl/)


═══════════════════════════════════════════════════════════════════════════════

Happy printing! 💝 Made with love for Valentine's Day 2026

═══════════════════════════════════════════════════════════════════════════════
