# 台灣溫度變化時間軸

互動式台灣溫度網格變化視覺化。

## Demo

線上展示：https://ianlkl11234s.github.io/taiwan-weather-timelapse/

## 功能特色

- 可拖曳的時間軸，支援播放/暫停
- 可調整播放速度 (1x, 2x, 4x, 8x)
- 日/夜指示器 (09:00-18:00 為白天)
- 平均溫度趨勢圖
- 滑鼠懸停顯示該格點溫度
- 自動循環播放

## 資料來源

- **提供者**：中央氣象署
- **資料集**：O-A0038-003（溫度網格分析）
- **解析度**：0.03 度（約 3.3 公里）
- **更新頻率**：每日自動更新（透過 GitHub Actions）
- **涵蓋範圍**：台灣本島及周邊海域

## 技術細節

### 記憶體消耗

目前實作會將所有溫度資料載入瀏覽器記憶體：

| 時間範圍 | 幀數 | JSON 大小 | 瀏覽器記憶體 | 狀態 |
|---------|------|----------|-------------|------|
| 1 週 | ~168 | ~7.7 MB | ~6 MB | 手機 OK |
| 2 週 | ~336 | ~15 MB | ~11 MB | 手機 OK |
| 1 月 | ~720 | ~22 MB | ~23 MB | 建議桌機 |

**網格規格：**
- 網格大小：120 x 67 = 8,040 格
- 有效格點（台灣陸地）：每幀約 3,495 格
- Leaflet 矩形物件：約 3,495 個（~0.7 MB）

**效能說明：**
- 現代桌機瀏覽器可輕鬆處理 200+ MB
- 手機瀏覽器可處理 50-100 MB
- 關閉頁面後記憶體會釋放
- 初次載入時間取決於網路速度（需下載 22 MB）

### 資料更新

資料透過 GitHub Actions 每日自動更新，時間為 UTC 06:00（台灣時間 14:00）：
1. 從 S3 取得最新溫度資料
2. 產生新的 `temperature_timelapse_data.json`
3. 部署至 GitHub Pages

## 本地開發

### 前置需求

- Python 3.8+
- boto3 (`pip install boto3 python-dotenv`)

### 設定步驟

1. Clone 專案：
   ```bash
   git clone https://github.com/ianlkl11234s/taiwan-weather-timelapse.git
   cd taiwan-weather-timelapse
   ```

2. 建立 `.env` 檔案並填入 S3 憑證：
   ```env
   S3_BUCKET=your-bucket-name
   S3_ACCESS_KEY=your-access-key
   S3_SECRET_KEY=your-secret-key
   S3_REGION=ap-northeast-1
   ```

3. 更新資料：
   ```bash
   python scripts/update_data.py
   ```

4. 啟動本地伺服器：
   ```bash
   cd public
   python3 -m http.server 8000
   ```

5. 開啟瀏覽器 http://localhost:8000

## 部署方式

### GitHub Pages

1. 在 repository 設定中啟用 GitHub Pages
2. 將 source 設為 `GitHub Actions`
3. 新增 repository secrets：
   - `S3_BUCKET`
   - `S3_ACCESS_KEY`
   - `S3_SECRET_KEY`
   - `S3_REGION`

GitHub Action 會自動：
- 每日 UTC 06:00 執行
- 從 S3 取得最新資料
- 部署至 GitHub Pages

### 其他平台

`public/` 目錄可部署至任何靜態網站託管服務：
- Vercel
- Netlify
- Cloudflare Pages

只需確保 `temperature_timelapse_data.json` 定期更新即可。

## 專案結構

```
taiwan-weather-timelapse/
├── .github/
│   └── workflows/
│       └── update-data.yml    # 每日資料更新
├── public/
│   ├── index.html             # 主視覺化頁面
│   └── temperature_timelapse_data.json  # 資料檔
├── scripts/
│   └── update_data.py         # 資料更新腳本
├── .env.example               # 環境變數範本
├── .gitignore
├── LICENSE
├── README.md
└── requirements.txt
```

## 授權

MIT License - 詳見 [LICENSE](LICENSE) 檔案。

## 致謝

- 溫度資料：[中央氣象署](https://www.cwa.gov.tw/)
- 地圖圖磚：[CARTO](https://carto.com/) / [OpenStreetMap](https://www.openstreetmap.org/)
- 地圖函式庫：[Leaflet.js](https://leafletjs.com/)
