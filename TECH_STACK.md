# Tech Stack

## Frontend
- Next.js 16 (App Router, static export)
- React 19
- Tailwind CSS v4
- Framer Motion
- Recharts
- **deck.gl 9** (hexagon, heatmap, GeoJSON, arc and text layers) for every map mode (3D,
  heatmap, district choropleth, satellite) over a MapLibre basemap via react-map-gl: keyless
  CARTO dark-matter / positron vector tiles and Esri World Imagery; raster CARTO tiles are the
  fallback if a style cannot be fetched.
  District boundaries ship as `web/public/geo/karnataka-districts.geojson` (31 districts) and the
  state outline as `karnataka-outline.geojson`.
- **CCTV feeds:** recorded Bengaluru street footage from Wikimedia Commons (`web/public/cctv/`,
  640x360 H.264) with YOLO11s + ByteTrack detections computed offline and replayed on a canvas.
- react-force-graph-2d / -3d + three.js (network view)

## Backend
- Python 3.13 + Flask
- Gunicorn (Catalyst AppSail)
- pandas / numpy
- networkx (knowledge graph, Adamic-Adar, Louvain)
- CSV-backed in-memory store over the 26-table CCTNS-shaped schema
- `appsail/investigation.py` — record-integrity checks, crime series (union-find),
  Rossmo geographic profiling, Heuer ACH, under-reporting (leave-one-out ridge,
  Wilson intervals), IO shortlisting — numpy/pandas only, every finding cited
- `appsail/statutes.py` — IPC ↔ BNS cross-walk keyed on the offence date

## AI / ML
- scikit-learn — the whole ML layer, no other modelling dependency
- Offline training entrypoint: `python -m ml.train`
- `artifacts/model_registry.json` is the published evidence the UI renders
- LLM copilot via GROQ_API_KEY / NVIDIA_API_KEY (optional; a rule-based
  fallback keeps the agent working without either)

## Geography
- `appsail/karnataka.py` — all 31 districts, 8 police commissionerates,
  140 stations, HQ coordinates and 2011 Census population/urbanisation.
  Single source of truth for the generator, the outcome simulator and the
  socio-economic overlay.
