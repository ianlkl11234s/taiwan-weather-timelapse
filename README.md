# 台灣溫度變化

互動式台灣溫度網格變化視覺化，呈現過去 30 天的溫度時間軸動畫。

## Demo

**線上展示**：[https://ianlkl11234s.github.io/taiwan-weather-timelapse/](https://taiwan-weather-timelapse.zeabur.app/)

## 功能特色

- 時間軸播放控制（播放/暫停/上一幀/下一幀）
- 可調整播放速度（1x, 2x, 4x, 8x）
- 日/夜時段指示（09:00-18:00 為白天）
- 平均溫度趨勢圖
- 滑鼠懸停顯示格點溫度
- 響應式設計，支援手機瀏覽

## 資料來源

- **提供者**：中央氣象署 氣象開放資料平台
- **資料集**：[O-A0038-003 溫度網格分析](https://opendata.cwa.gov.tw/dataset/observation/O-A0038-003)
- **解析度**：0.03°（約 3.3 公里）
- **原始更新頻率**：每小時
- **本站資料更新**：每日一次，保留最近 30 天

## 專案架構

```
taiwan-weather-timelapse/
├── .github/workflows/
│   └── update-data.yml           # GitHub Actions 每日自動更新
├── public/
│   ├── index.html                # 主視覺化頁面
│   └── temperature_timelapse_data.json  # 溫度資料檔
├── scripts/
│   └── update_data.py            # 資料更新腳本
└── README.md
```

## 使用技術

### 前端
- **Leaflet.js**：互動式地圖
- **原生 JavaScript**：播放控制、資料渲染
- **CSS3**：響應式佈局、動畫效果

### 資料流程
```
氣象開放資料平台 (每小時)
        ↓
   定時排程抓取
        ↓
     AWS S3 儲存
        ↓
  GitHub Actions (每日)
        ↓
   GitHub Pages 部署
```

### 底圖
- **圖磚提供**：[CARTO](https://carto.com/)
- **地圖資料**：[OpenStreetMap](https://www.openstreetmap.org/)
- **授權標註**：依據 OpenStreetMap 授權條款，需標註 `© OpenStreetMap contributors`

## 本地開發

### 啟動步驟

1. Clone 專案：
   ```bash
   git clone https://github.com/ianlkl11234s/taiwan-weather-timelapse.git
   cd taiwan-weather-timelapse
   ```

2. 啟動本地伺服器：
   ```bash
   cd public
   python3 -m http.server 8000
   ```

3. 開啟瀏覽器 http://localhost:8000

> **注意**：資料更新功能需要 S3 存取權限，目前僅限維護者執行。

## 授權

MIT License

## 致謝

- 溫度資料：[中央氣象署](https://www.cwa.gov.tw/)
- 地圖圖磚：[CARTO](https://carto.com/) / [OpenStreetMap](https://www.openstreetmap.org/)
- 地圖函式庫：[Leaflet.js](https://leafletjs.com/)
