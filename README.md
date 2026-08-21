# NiceShot

<p align="center">
  <img src="app/assets/icon.png" width="128" alt="NiceShot">
</p>

<p align="center">轻量 Windows 截图工具：窗口选中、像素级框选、OCR 与中英互译。</p>

用 conda 环境 **scz1**（Python 3.10）运行，托盘常驻，默认快捷键 `Ctrl+Shift+A`。

## 功能

- 自定义截图快捷键（托盘里按组合键录制）
- 开机自动启动（无黑框）
- 悬停高亮窗口，单击选中整个窗口；按住左键拖拽框选
- 光标旁放大器：像素网格、坐标、RGB
- 框选后底部工具条：识别文字 / 翻译 / 确认勾
- 确认后复制到剪贴板（图片 + 文件），可粘贴到任意文件夹得到 PNG
- 任意位置右键或 `Esc` 取消
- 识别框选范围内的中英文
- 智能翻译：中文 → 英文，英文 → 中文（MyMemory，无需 API Key）

## 环境

- Windows 10 / 11
- conda 环境 `scz1`（Python 3.10）
- 依赖见 [requirements.txt](requirements.txt)

```powershell
conda activate scz1
pip install -r requirements.txt
```

## 启动

在项目根目录执行：

```powershell
conda activate scz1
python main.py
```

启动后出现在系统托盘。图标使用 [app/assets/icon.png](app/assets/icon.png)（托盘 / 窗口 / 开机快捷方式同步使用 [app/assets/icon.ico](app/assets/icon.ico)）。

托盘菜单：

- **开始截图**（也可双击托盘图标）
- **设置快捷键**
- **开机启动**
- **退出**

配置保存在 `%APPDATA%\NiceShot\config.json`。

## 截图操作

1. 按下快捷键进入全屏遮罩
2. 鼠标悬停会高亮当前窗口，单击即选中该窗口
3. 按住左键拖拽则自由框选；放大器用于像素级对齐
4. 选区右下角点绿色勾，截图进入剪贴板
5. 在资源管理器中 `Ctrl+V` 可直接保存为 `NiceShot_日期时间.png`
6. 点「识别文字」或「翻译」会弹出结果窗口，可一键复制文本

第一次使用识别 / 翻译时会加载 OCR 模型，可能稍慢。免费翻译接口有日配额，超限时会提示。
