# Taiwan Temperature Timelapse

Interactive visualization of Taiwan's temperature grid changes over time.

## Demo

View the live demo at: [GitHub Pages URL after deployment]

## Features

- Interactive timeline with play/pause controls
- Adjustable playback speed (1x, 2x, 4x, 8x)
- Day/Night indicator (09:00-18:00 = Day)
- Temperature trend chart
- Hover to see temperature at specific grid points
- Auto-loops through all available data

## Data Source

- **Provider**: Central Weather Administration (CWA), Taiwan
- **Dataset**: O-A0038-003 (Temperature Grid Analysis)
- **Resolution**: 0.03 degrees (~3.3 km)
- **Update Frequency**: Daily (via GitHub Actions)
- **Coverage**: Taiwan and surrounding waters

## Technical Details

### Memory Consumption

The current implementation loads all temperature data into browser memory:

| Time Range | Frames | JSON Size | Browser Memory | Status |
|------------|--------|-----------|----------------|--------|
| 1 week | ~168 | ~7.7 MB | ~6 MB | Mobile OK |
| 2 weeks | ~336 | ~15 MB | ~11 MB | Mobile OK |
| 1 month | ~720 | ~22 MB | ~23 MB | Desktop recommended |

**Grid specifications:**
- Grid size: 120 x 67 = 8,040 cells
- Valid cells (Taiwan land): ~3,495 per frame
- Leaflet rectangles: ~3,495 objects (~0.7 MB)

**Performance notes:**
- Modern desktop browsers handle 200+ MB easily
- Mobile browsers can handle 50-100 MB
- Memory is released when page is closed
- Initial load time depends on network speed (22 MB download)

### Data Update

Data is updated daily via GitHub Actions at 06:00 UTC (14:00 Taiwan time):
1. Fetch latest temperature data from S3
2. Generate new `temperature_timelapse_data.json`
3. Deploy to GitHub Pages

## Local Development

### Prerequisites

- Python 3.8+
- boto3 (`pip install boto3 python-dotenv`)

### Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/weather_change.git
   cd weather_change
   ```

2. Create `.env` file with S3 credentials:
   ```env
   S3_BUCKET=your-bucket-name
   S3_ACCESS_KEY=your-access-key
   S3_SECRET_KEY=your-secret-key
   S3_REGION=ap-northeast-1
   ```

3. Update data:
   ```bash
   python scripts/update_data.py
   ```

4. Start local server:
   ```bash
   cd public
   python3 -m http.server 8000
   ```

5. Open http://localhost:8000 in your browser

## Deployment

### GitHub Pages

1. Enable GitHub Pages in repository settings
2. Set source to `gh-pages` branch
3. Add repository secrets:
   - `S3_BUCKET`
   - `S3_ACCESS_KEY`
   - `S3_SECRET_KEY`
   - `S3_REGION`

The GitHub Action will automatically:
- Run daily at 06:00 UTC
- Fetch latest data from S3
- Deploy to GitHub Pages

### Other Platforms

The `public/` directory can be deployed to any static hosting:
- Vercel
- Netlify
- Cloudflare Pages

Just ensure `temperature_timelapse_data.json` is updated regularly.

## Project Structure

```
weather_change/
├── .github/
│   └── workflows/
│       └── update-data.yml    # Daily data update
├── public/
│   ├── index.html             # Main visualization
│   └── temperature_timelapse_data.json  # Data file
├── scripts/
│   └── update_data.py         # Data update script
├── .env.example               # Environment template
├── .gitignore
├── README.md
└── requirements.txt
```

## License

MIT License

## Credits

- Temperature data: [Central Weather Administration](https://www.cwa.gov.tw/)
- Map tiles: [CARTO](https://carto.com/) / [OpenStreetMap](https://www.openstreetmap.org/)
- Map library: [Leaflet.js](https://leafletjs.com/)
