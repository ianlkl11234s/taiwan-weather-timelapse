# 台灣氣象變化

互動式台灣氣象網格變化視覺化，呈現過去 30 天的溫度、濕度與氣壓時間軸動畫。

## Demo

**線上展示**：https://taiwan-weather-timelapse.zeabur.app/

## 功能特色

- **三種氣象圖層**：可切換顯示溫度、濕度或氣壓分布
- **觀測站圖層**：可疊加顯示 835 個氣象測站位置
- **比較模式**：左右並排同時檢視任意兩種氣象資料變化（桌面版）
- 時間軸播放控制（播放/暫停/上一幀/下一幀）
- 可調整播放速度（1x, 2x, 4x, 8x）
- 日/夜時段指示（09:00-18:00 為白天）
- 平均數值趨勢圖
- 滑鼠懸停顯示格點數值
- 響應式設計，支援手機瀏覽

## 資料來源

### 溫度資料
- **提供者**：中央氣象署 氣象開放資料平台
- **資料集**：[O-A0038-003 溫度網格分析](https://opendata.cwa.gov.tw/dataset/observation/O-A0038-003)
- **解析度**：0.03°（約 3.3 公里）
- **原始更新頻率**：每小時

### 濕度資料
- **提供者**：中央氣象署 氣象測站資料
- **資料來源**：約 835 個氣象測站的即時觀測資料
- **處理方式**：空間內插至 120×67 網格
- **解析度**：0.03°（約 3.3 公里），與溫度網格一致

### 氣壓資料（海平面氣壓）
- **提供者**：中央氣象署 氣象測站資料
- **資料來源**：約 835 個氣象測站的即時觀測資料
- **處理方式**：測站氣壓換算為海平面氣壓 (SLP) 後，空間內插至 120×67 網格
- **解析度**：0.03°（約 3.3 公里），與溫度網格一致
- **單位**：百帕（hPa）

#### 為什麼使用海平面氣壓？

測站氣壓會受海拔高度影響（高山氣壓低、平地氣壓高），若直接內插會變成「地形圖的負片」。換算為海平面氣壓後：
- 消除地形影響，呈現真正的氣壓系統分布
- 數值變化平滑，內插效果更好
- 與氣象局「地面天氣圖」使用相同標準

#### 換算公式

使用氣象學標準公式（Barometric formula）：

```
P_sl = P_st × (1 - 0.0065 × h / (T + 0.0065 × h + 273.15))^(-5.257)
```

- `P_sl`：海平面氣壓
- `P_st`：測站氣壓
- `h`：海拔高度（公尺）
- `T`：氣溫（°C）

---

## 內插法說明

濕度與氣壓資料使用 `scipy.interpolate.griddata` 的 **cubic（三次）內插法**，將離散的氣象測站資料內插至連續網格：

```python
from scipy.interpolate import griddata
grid_data = griddata(points, values, (grid_lon, grid_lat), method='cubic')
```

**為什麼選擇 cubic 內插？**
- 產生平滑且連續的曲面，視覺效果較佳
- 比 linear 內插更能捕捉局部變化趨勢
- 適合氣象資料這類空間上連續變化的物理量

### 可能的偏誤與限制

#### 濕度資料

1. **測站分布不均**：氣象測站主要集中在平地與都市區域，山區測站較少，導致高海拔地區的濕度估計可能不夠準確。

2. **邊緣效應**：在資料邊緣（如海岸線附近），cubic 內插可能產生不合理的外插值，因此已套用陸地遮罩過濾海洋區域。

3. **極端值處理**：內插後的數值已限制在 0-100% 範圍內，但在測站稀疏區域仍可能出現不自然的平滑效果。

4. **時間解析度**：濕度變化通常比溫度更劇烈，每小時一次的取樣可能無法完整呈現短時間內的變化。

5. **地形影響**：山區迎風面與背風面的濕度差異可能因測站不足而被低估。

#### 氣壓資料

1. **SLP 換算依賴氣溫**：海平面氣壓的計算需要同時有氣壓、海拔、氣溫三個數值，缺少任一項的測站會被排除。

2. **高海拔測站的不確定性**：海拔越高，換算公式的假設偏差越大。超過 3000 公尺的測站數據可能較不準確。

3. **空間平滑性**：海平面氣壓在空間上變化相對平滑，因此 cubic 內插效果良好。

4. **數值範圍**：內插後的數值已限制在 900-1100 hPa 範圍內，以過濾不合理的極端值。

5. **局部氣壓系統**：小範圍的氣壓變化（如雷雨胞）可能因測站密度不足而無法完整呈現。

---

### 資料更新
- **更新頻率**：每日一次（GitHub Actions 自動執行）
- **保留期間**：最近 30 天

## 專案架構

```
taiwan-weather-timelapse/
├── .github/workflows/
│   ├── deploy.yml                # GitHub Pages 部署
│   └── update-data.yml           # 每日資料自動更新
├── public/
│   ├── index.html                # 主視覺化頁面
│   ├── temperature_timelapse_data.json  # 溫度資料
│   ├── humidity_timelapse_data.json     # 濕度資料
│   ├── pressure_timelapse_data.json     # 氣壓資料
│   └── stations.json             # 觀測站位置資料
├── scripts/
│   ├── update_data.py            # 溫度資料更新腳本
│   ├── update_humidity.py        # 濕度資料更新腳本
│   ├── update_pressure.py        # 氣壓資料更新腳本
│   └── requirements.txt          # Python 依賴套件
└── README.md
```

## 使用技術

### 前端
- **Leaflet.js**：互動式地圖
- **原生 JavaScript**：播放控制、資料渲染
- **CSS3**：響應式佈局、動畫效果

### 後端處理
- **Python**：資料擷取與處理
- **scipy**：空間內插（griddata）
- **numpy**：數值運算
- **boto3**：AWS S3 資料存取

### 資料流程
```
氣象開放資料平台 (每小時)
        ↓
   定時排程抓取
        ↓
     AWS S3 儲存
        ↓
  GitHub Actions (每日)
   ├── 溫度：直接使用網格資料
   ├── 濕度：測站資料 → 空間內插
   └── 氣壓：測站資料 → SLP 換算 → 空間內插
        ↓
   GitHub Pages 部署
```

### 底圖
- **圖磚提供**：[CARTO](https://carto.com/)
- **地圖資料**：[OpenStreetMap](https://www.openstreetmap.org/)

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

- 氣象資料：[中央氣象署](https://www.cwa.gov.tw/)
- 地圖圖磚：[CARTO](https://carto.com/) / [OpenStreetMap](https://www.openstreetmap.org/)
- 地圖函式庫：[Leaflet.js](https://leafletjs.com/)
- 內插運算：[SciPy](https://scipy.org/)
