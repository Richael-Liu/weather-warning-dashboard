# 中国天气网预警列表页自动汇总版

这个版本不再使用心知天气 API，也不需要付费 API key。

它的思路是：

```text
自动访问中国天气网预警列表页
↓
按省份抓取当前预警列表
↓
解析预警标题中的类型和等级
↓
生成一张“全国各省预警一页图”
```

## 运行方式

安装依赖：

```bat
pip install -r requirements.txt
```

运行：

```bat
python weathercomcn_warning_dashboard.py
```

生成结果在：

```text
output/
├── weathercomcn_alarms_时间.csv
├── weathercomcn_province_counts_时间.csv
└── 全国各省预警一页图_时间.png
```

## Windows 每天自动运行

编辑：

```text
run_daily_weathercomcn_windows.bat
```

然后双击测试。成功后放入 Windows 任务计划程序即可。

## 注意

这不是中国天气网公开授权 API，而是网页列表页自动抓取。  
如果只是你自己每天内部查看，技术上比较适合；如果要公开发布、商用、做平台产品，请联系中国天气网/中国气象局确认授权。

## 如果和中国天气网地图页有差异

这个脚本抓的是中国天气网的“列表页”，不是地图图标页。一般会比第三方 API 更接近中国天气网，但如果地图页和列表页本身存在缓存时间差，仍可能有几分钟到几十分钟差异。
