# LC Mapping Lab

将液晶信息 **G(x,y,t)** 和随机运动概率 **P(x,y,t)** 导入浏览器，按原始 Python 算法计算映射矩阵 **A**，查看验证结果并下载矩阵与报告。界面为中文，静态网站可部署到独立的 GitHub Pages 网址；无需 ChatGPT 或后端服务器。

在线使用：[LC Mapping Lab](https://ruiyikevinkang.github.io/LC-images/)。公共源码仓库：[RuiyiKevinKang/LC-images](https://github.com/RuiyiKevinKang/LC-images)。项目自有代码使用 MIT 许可；第三方运行环境保留各自许可，见 [THIRD_PARTY.md](THIRD_PARTY.md)。网站内提供源码下载，部署流程会自动更新该压缩包。

## 使用

1. 下载网页内的输入模板，按模板填写 G 和 P。支持 XLSX、CSV、TSV、JSON、NPY、NPZ；不同文件格式须遵守相应数据结构，不能仅改扩展名。
2. 导入两个文件，确认时间轴、空间分箱宽度及概率处理设置。默认分箱宽度为 16；输入 G 是绿色通道强度，不是 RGB 彩色图。
3. 开始求解。成功后查看矩阵尺寸、秩、拟合误差和概率质量误差，再下载结果。验证失败时会显示原因。

首次使用需要联网从网站同源加载固定版本的 **Pyodide 0.27.7** 及依赖，下载量约数十 MB；之后加载速度取决于浏览器缓存。运行环境使用 NumPy 2.0.2、micropip 0.9.0、packaging 24.2、openpyxl 3.1.5 和 et_xmlfile 2.0.0。计算在 Web Worker 中运行，可取消当前任务。输入数据及计算结果在浏览器内处理；网站不设数据上传接口，浏览器不需要访问 PyPI 或第三方 CDN 来安装依赖。

当前浏览器实现设有输入大小及计算规模限制：每个文件上限 40 MB、时间帧数上限 600、单个数值数组以及 F、A 分别上限 2,000,000 个元素。XLSX 输入上限 500,000 个单元格，压缩输入的解压体积上限 64 MB、最多 200 个条目。实际耗时和可用规模还受设备内存影响；较大的原始数据可使用随附 Python 脚本在本机处理。

## 输入与输出

网页提供 `demo_G.xlsx`、`demo_P.xlsx` 配套样例，以及 G/P 的 CSV、JSON 模板。G 和 P 可以使用不同文件格式。

| 格式 | G 输入 | P 输入 |
| --- | --- | --- |
| XLSX / CSV / TSV 长表 | `t,x,y,G`，每行一个时刻的一个像素 | `t,x,y,P`，每行一个时刻的一个网格 |
| XLSX / CSV / TSV 宽表 | `pixel_id,x,y,G_frame_000,G_frame_001,…`，每行一个像素 | `time,p0,p1,…` 或 `frame,p0,p1,…`，每行一帧；无时间时可只有概率列 |
| JSON / NPZ | `G` 为 `(T,H,W)` 图像或配合 `xy` 的像素列表；可提供 `time`。已分箱缓存使用 `mean_green` 为 `(T,B)` 或 `(V,T,B)` | `P` 为 `(T,K)` 或 `(T,H,W)`，可提供 `xy,time,kind,cell_area` |
| NPY | `(T,H,W)` 绿色通道图像序列 | `(T,K)` 概率矩阵或 `(T,H,W)` 概率图像序列 |

表格中的 `t`、`time`、`time_s`、`LC_time_s` 表示物理时间；`frame` 表示帧索引。无时间的数组默认按帧索引对齐；不要把只有一侧提供物理时间的数据默认为同时刻。XLSX 使用第一个数据工作表，跳过 `metadata`、`readme`、`说明`；可选 `metadata` 工作表使用 `key,value` 列，`time` 的值为 JSON 时间数组。模板提供可直接复用的布局。

已分箱的 `mean_green` 缓存沿用输入分箱次序，不重新做空间分箱。若需要物理坐标对应，优先提供完整的原始 G/xy 信息并检查导出的特征元数据。

通过验证的结果可以单独下载或下载 `result.zip`：

- `A.csv`、`A.npy`：完整映射矩阵，行对应活动概率网格，列对应当前/前 1/前 2 帧的分箱特征。
- `A_factors.npz`：L、R 与求解元数据；满足 A = L @ R。
- `prediction.csv`：拟合概率，恢复输入 P 的完整网格列，包括被移出的恒零列。
- `feature_metadata.csv`、`target_cells.csv`：矩阵列与分箱/历史的对应、矩阵行与目标网格的对应。
- `report.json`：参数、数值指标、验证结果及 `active_indices`；该索引将 A 的活动行映射回原 P 网格。
- `result.xlsx`：规模不超过 200,000 个导出单元格时提供的工作簿；较大结果使用 CSV、NPY 或 NPZ。

输入无效或数值验证未通过时，仅提供拒绝报告及包含报告的 ZIP，不输出可被误认为合格解的矩阵。

## 数学与边界

设 T 为帧数、B 为有效空间分箱数、K 为活动概率网格数。对 G 做固定均匀空间分箱，得到每帧分箱均值 g(t)；构造：

```text
F[t] = [g(t), g(max(t−1, 0)), g(max(t−2, 0))]
F.shape = (T, 3B)
P.shape = (T, K)
A.shape = (K, 3B)
P̂ = F @ A.T
A = L @ R
```

求解复用 `site/python/solve_A.py` 与 `site/python/bin_pixels.py`，这两个原始文件保持不变。浏览器适配层负责文件解析、参数校验和结果导出；核心采用 SVD 伪逆、目标 SVD 因子、最小范数基解、折入 L 的微小非负修正及 QR 秩检查，不添加岭回归或神经网络训练。

- **F 必须满行秩**，即 rank(F) = T；不满足时拒绝求解，不以改变算法补齐秩。因此 T ≤ 3B 只是必要条件，重复帧或相关特征仍可能使输入不满足要求。
- P 必须有限且非负，每帧概率质量和应为 1。只有显式选择归一化时，才按每帧输入质量重新归一化；密度须提供网格面积才能转换为概率质量。
- G/P 时间须明确对齐。线性插值须显式启用，且不允许超出 P 时间范围外推。
- 全部帧始终为零的概率网格会被移出活动网格，输出中保留网格对应关系。
- 这是对输入帧求解的映射。**未见帧上的非负性、概率和及预测精度不作保证**；小的输入扰动也可能在病态矩阵下放大。

## 本地运行

在本项目目录执行（Python 3.10 或以上）：

```sh
python tools/vendor_runtime.py
python tools/package.py
python -m http.server 8000 --directory site
```

浏览器打开 `http://localhost:8000`。不要直接双击 `index.html`，浏览器会限制本地文件的 Worker 与资源加载。无需安装 `requirements.txt` 即可使用网页；该文件仅供原始脚本及数据工具的本机 Python 环境使用。

`tools/vendor_runtime.py` 在构建时从官方 Pyodide 分发站点、PyPI 及上游源码仓库下载固定版本运行环境，校验包文件 SHA256，并保存来源、哈希和许可证材料。生成文件在 `site/vendor/pyodide/`，不提交到 Git，也不重复放入源码 ZIP；下载源码后重新运行该工具即可恢复运行环境。运行环境的完整记录为 `site/vendor/pyodide/VENDOR-MANIFEST.json`。

## 开源并部署到 GitHub Pages

1. 本项目公共仓库为 [RuiyiKevinKang/LC-images](https://github.com/RuiyiKevinKang/LC-images)。如需独立部署，Fork 或创建 Public 仓库，将本目录推送到 `main` 分支；根目录应直接包含 `site/`、`tools/` 和 `.github/`。
2. 在仓库的 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。
3. 推送代码或在 **Actions → Deploy LC Mapping Lab to GitHub Pages → Run workflow** 手动运行。随附工作流会下载并校验固定版本运行环境、生成源码下载包并发布 `site/`。
4. 工作流成功后，从部署记录读取实际网站地址，通常为 `https://<用户名>.github.io/<仓库名>/`。公共源码仓库地址为 `https://github.com/<用户名>/<仓库名>`。

线上页面与源码仓库均独立于 ChatGPT。向自己的仓库首次发布需要有权启用 Pages 的 GitHub 账户；公共仓库中的项目源码与模板会公开，不应提交个人实验数据。

GitHub 的设置步骤见[使用自定义工作流部署 GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)。Pyodide 运行环境见[官方文档](https://pyodide.org/en/0.27.7/usage/loading-packages.html)。

## 项目内容

```text
site/                       静态网页、Worker、模板、原始 Python 算法
site/python/solve_A.py      原始求解脚本
site/python/bin_pixels.py   原始空间分箱与历史特征脚本
tools/vendor_runtime.py     下载、校验并记录固定版本浏览器运行环境
tools/package.py            打包完整源码并生成 site/source.zip
tests/test_bridge.py        文件适配与原算法的集成验证
.github/workflows/pages.yml GitHub Pages 自动发布流程
requirements.txt            可选本机 Python 依赖
LICENSE                     MIT 许可
THIRD_PARTY.md               第三方运行环境及许可证说明
```

运行 `python tools/package.py` 会生成项目同级的 `lc-mapping-lab-source.zip`，并复制为网站的 `site/source.zip`。打包排除版本控制目录、虚拟环境、缓存、日志、下载的第三方运行环境及已生成的源码压缩包。

## 验证

文件适配层包含 11 项本机集成测试，验证多种输入解析、与原算法结果的一致性，以及秩亏和无效概率等拒绝情况。安装可选依赖后运行：

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

2026-10-16 验证：11 项集成测试通过；浏览器同源运行环境加载、XLSX 演示求解及 ZIP 下载通过。8 帧演示得到 A=(9,36)、rank(A)=8，最大拟合误差约 2.6×10⁻¹⁵，概率和最大误差约 1.1×10⁻¹⁵。GitHub Pages 首次部署成功，公开网址返回 HTTP 200。不同设备和输入的结果仍须依据每次检验报告判断。
