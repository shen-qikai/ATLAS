# ATLAS 增量良率流水线（v1.4.0）

## 目录与入口

所有真实数据、缓存、清理结果、报表和日志必须在 Git 仓库之外。例如：

```text
D:/FT_DATA/
├── LDO/
│   └── WL111/
│       ├── LOT001/
│       │   ├── WL111_LOT001_1#_CP.csv
│       │   ├── WL111_LOT001_1#_RT1.csv
│       │   └── summary_cleaning_data/
│       │       └── WL111_LOT001_1#_summary_cleaning.csv
│       ├── LOT002/
│       ├── WL111_BIN良率.xlsx
│       ├── WL111_测试项失效率.xlsx
│       ├── WL111_测试项平均值.xlsx
│       └── .atlas/
│           ├── state.sqlite
│           └── logs/
├── DCDC/
├── Load_switch/
└── _atlas_runs/
```

运行 `python main.py`，选择 **自动增量良率** 标签页：

1. 选择 LDO/DCDC/Load_switch 的父目录。
2. 选择“增量处理”，点击“扫描 / Preview”。
3. 核对产品、Lot、Wafer、输入文件数和处理动作。
4. 关闭 Excel 中打开的产品报表，点击“执行”。

入口是人工选择目录并执行，暂不后台监控、不联网拉取数据、不解压、不改原始文件名。
初次运行不会导入旧 Excel 作为历史；会计算当前目录中的所有可识别 Wafer 建立缓存。

## Product Profile

产品文件夹名称必须对应 `config/products.yaml` 中启用的 Product。
只有一个顶层 `products:`；新增产品时在其下面新增条目，不要再写第二个 `products:`。
使用单引号保存 Regex，examples 中 wafer 使用引号保留前导零。

下面是匿名化的完整配置样例（在现有 products 下面添加 WL111）：

```yaml
products:
  WL111:
    enabled: true
    naming:
      version: 1
      regex: '^WL111_(?P<lot>[^_]+)_(?P<wafer>\d+)#_(?P<stage>CP|RT\d+).*\.csv$'
      examples:
        - input: 'WL111_LOT001_1#_CP.csv'
          lot: 'LOT001'
          wafer: '1'
        - input: 'WL111_LOT001_1#_RT1.csv'
          lot: 'LOT001'
          wafer: '1'
    validation:
      lot_required: true
      wafer_required: true
      wafer_min: 1
      wafer_max: 25
    cleaning:
      mode: coord
    columns:
      header: SITE_NUM
      pass_fail: PASSFG
      x: X_COORD
      y: Y_COORD
      soft_bin: SOFT_BIN
    yield:
      good_bins: [1]
      denominator: TotalCount  # 历史字段，失效率固定使用晶圆总die数量
    wafer:
      expected_die_count: 10000
      die_count_tolerance: 50
    automation:
      enabled: true
      test_stage: CP  # 历史兼容字段，不代表实际测试阶段
```

根目录的 LDO/DCDC/Load_switch 只是分类，不决定清理算法。产品级设置覆盖
`pipeline_defaults`；未设置时采用坐标清理、BIN1=Good。
不从文件名推断 CP/FT，不显示测试阶段。`test_stage` 和 `denominator` 保留以兼容旧配置，
无需修改；它们不决定新的测试项失效率口径。命名 Regex 仍须匹配实际文件名。
不同设计改版应使用新的产品记录/目录，避免混合统计。

现有 WS0712 Profile 保留了源文件产品名与配置产品名不同的情况：以 WS0712
作为产品文件夹，Naming Regex 负责识别其源文件。Lot 文件夹名称必须与文件解析的 Lot 一致。
已经使用产品配置名规范命名的 `Product_Lot_Wafer#_...csv` 可以直接识别；其他格式使用 Naming Regex。
自动入口只读取每个 Lot 目录的直接 CSV，跳过输出子目录和已生成的 summary/onlydata 文件。
存在无法识别/身份冲突文件时，阻断该 Lot，其他合法 Lot/产品可继续。

## 清理与格式约定

- `coord`：保留按测试顺序最后出现的同坐标记录，坐标必须有效。
- `rt`：沿用原手动算法；有 RT 时保留初测 Pass 和全部 RT 记录，没有 RT 时全留。
  这不是“每颗 die 最终结果”的新算法；重复坐标会单独提示。
- RT 通过明确的 `RT/RT1/RT2` 后缀标记识别，不采用产品名内任意 RT 子串。
- 多文件优先按 Ending Time 排序；缺时间时按初测/明确 RT 序号排序。
  同轮次坐标重叠且先后不明时拒绝猜测，需要补充正确时间/轮次信息。
- 当前 CSV 必须包含表头、随后四行单位/LSL/USL/其他信息和测试记录。
  末尾无名列仅在元信息和全部数据都为空时忽略；含实际内容的无名列提示列号/行号并
  要求人工确认。中间无名列和重复列名仍拒绝，不修改原始 CSV。
  支持 UTF-8/GBK，要求必需列、唯一列名、明确 Pass/Fail 值，同 Wafer 的列和元信息一致。
- 只写整合清理 CSV，保留五行表头结构、source_file 和 ending_time 溯源列。
  不生成未清理 merge 或 onlydata；不会删除已有历史产物。

## 增量判断与恢复

业务唯一标识为产品目录中的 `(Lot, Wafer)`。`01` 与 `1` 是同一 Wafer。
旧缓存中唯一匹配的内部键继续复用，新片使用中性内部标识，不猜测实际测试阶段。
若同一产品/Lot/Wafer 存在多套历史标识，要求人工分开目录，不自动合并。
真正不同的 CP/FT 数据不能放在同一产品/Lot/Wafer 输入集合，应使用独立数据根目录。
RT/分段文件属于同一 Wafer 输入集合，不会新增一行“RT Wafer”。

输入集合新增/变化、清理或统计配置改变、cleaned 文件消失/变化时，只重算相关 Wafer。
原文件内容不变但修改时间变化，只更新指纹，不重新计算。
默认利用文件大小和修改时间复用 SHA256，避免重复读取历史大文件。
若担心同大小、同时间的内容被替换，选择“完整校验文件内容”或 `--verify-hashes`。
所有复制操作应先完成再运行；源文件处理期间发生变化会拒绝提交结果。

`.atlas/state.sqlite` 保存状态、输入映射和小型指标，不保存原始 die 明细。
每片成功结果独立提交。失败时保留旧指标，但标记 failed 并排除主表，日志/处理状态 Sheet 明示。
发现输入消失时标记 missing，不删除原始/历史清理文件，也不静默保留旧指标作为有效结果。
若同一 Wafer 仅部分已登记文件消失，也会阻断重算，防止仅用剩余 RT/分段文件产生残缺结果；
请恢复文件后重试，强制重算也不会绕过此完整性检查。
产品级 OS 锁阻止两个任务同时写同一产品；进程退出后锁自动释放。

三个 Excel 是缓存的展示结果，按稳定 Wafer 标识新增/替换，不直接向 Excel 末尾盲目追加。
报表丢失或上次导出失败可从缓存重建。历史 Wafer 不重算；有变化时重新写三个 Excel。
所有三个报表先写临时文件，再替换；失败时尝试恢复旧报表，保持缓存导出状态待重试。
不要手工编辑自动报表作为新的数据来源，否则后续更新会覆盖编辑。

## 报表与数量校验

每个产品三个 Excel 主表每片 Wafer 一行：

- BIN良率：总良率（默认 BIN1）、各 BIN 占比。
- 测试项失效率：失效die数量 / 晶圆总die数量；低于 LSL 或高于 USL 为失效，
  等于边界通过，单边规格只比较存在的一侧，无规格为 No_SPEC。
  缺测/非有限数值不计失效，但仍在晶圆总die分母中，并在“测试项缺测die数”单独显示。
  有有效坐标时按同坐标最后一条保留记录计算；无坐标只能按记录计数并提示无法识别重复die。
- 测试项平均值：有限有效数字的均值，文本/无有效数字为 NA。

都包含有效die数量、晶圆总die数量、保留记录数、BIN有效记录数、期望die数量、差值、数量状态、规格版本等。
有效die优先为唯一有效坐标数；无坐标时采用有效BIN记录数并标注方法，不能证明重复die是否存在。
期望值未设置时仍显示实际数量，但不会猜测“应该有多少颗”。偏差在配置容差内属正常提示，
超出容差只是警告，不自动拒绝数据或推断漏测。BIN分母保留原来的清理后总记录数口径。
RT 模式若保留重复坐标，记录数与有效die数量可能不同，请根据实际业务规则检查。

测试项两类报表另有“测试项有效数”（有效测试记录数）“测试项规格”Sheet；失效率报表另有
“测试项失效die数”和“测试项缺测die数”。新增测试项自动并入列集合，
不同规格保留版本并提示查规格表，不用首片规格静默覆盖其他规格。
每个报表还有“处理状态”和“数据说明”。日志位于根目录 `_atlas_runs` 与产品 `.atlas/logs`，
记录输入文件映射/指纹、成功、跳过、失败、不可用结果、数量警告和报表更新状态。

升级后正常执行一次增量处理即可生成新失效率报表；原“测试项良率”文件不再更新，
保留为历史文件，可自行归档。通常复用旧缓存；若旧 RT 指标含重复坐标而缺少按die统计，
会提示 METRICS_UPGRADE 并只重算这些片。此情况不能只用 `--rebuild-reports`。
BIN 良率、测试项平均值及原手动统计入口的算法不在本次修改范围内。

## CLI

```powershell
python run_pipeline.py 'D:/FT_DATA' --preview
python run_pipeline.py 'D:/FT_DATA'
python run_pipeline.py 'D:/FT_DATA' --force
python run_pipeline.py 'D:/FT_DATA' --rebuild-reports
python run_pipeline.py 'D:/FT_DATA' --verify-hashes
```

`--config` 可指定仓库之外的私有产品配置。`--preview` 不创建缓存/输出/日志。
`--rebuild-reports` 只投影已有缓存，不重新检查原始输入是否新增/消失；要更新输入状态请使用增量运行。
失败或存在不可用 Wafer 返回非零退出码，便于未来调度，但本版本不创建定时任务。

## 测试

```powershell
python -m unittest discover -s tests -v
```

所有 CSV/XLSX 测试样本仅在 OS 临时目录生成，使用匿名化合成数据，不能提交生产数据。
