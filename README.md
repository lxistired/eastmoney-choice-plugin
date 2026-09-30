# 东方财富Choice"野生"数据插件构建方法

> 我给Claude搭了一个东方财富Choice量化终端的数据插件——不是官方合作，纯靠逆向工程。爬了130万条指标建索引，让大模型也能像人类分析师一样，一句话就从金融终端拉数据、画图、写报告。

## 背景：大模型的数据盲区

大模型可以直接读年报PDF、抓取网页数据，但金融终端里的标准指标（社融、M2、银行净息差……）如果还让大模型去啃PDF，就走错了方向：一份PDF几十页消耗大量Token，而且不可能从一个PDF里凑出完整的历史时间序列。

**思路很清楚：终端有的数据走终端API，终端没有的数据走PDF提取，两条路互补。**

|  | 从PDF/网页提取 | 从金融终端API拉取 |
|---|---|---|
| 适用场景 | 终端未收录的特殊数据 | 标准宏观/个股指标 |
| 耗时 | 30-60分钟 | ~10分钟 |
| 时间序列 | 难以拼出连续序列 | API直接返回任意时间段 |
| Token消耗 | 大（PDF占上下文） | 小（结构化JSON） |

## 核心问题：130万条指标，没有一本目录

东方财富提供了一个Python API（EMQuant），可以调函数拉任意指标数据。但问题是——**它没有公开数据字典**。你知道社融增量、PPI一定在系统里，但它们的指标代码是什么？在互联网上搜不到，只能人工去终端里一个一个找。

> 这就像拿到了一座130万册藏书的图书馆钥匙，推门进去发现——没有目录，没有索引，书架上也没有标签。你知道书就在某个架子上，但不知道该往哪找。

我们做了一件笨功夫：把东方财富的在线查询页面整个爬了下来——逐页解析指标名称、代码、频率、单位，一条一条录入。相当于**给这座没有目录的图书馆，自己手写了一份完整的索引卡片**。

### 最终成果

- **138万条** EDB宏观指标（GDP、CPI、M2、社融、利率、汇率……全覆盖）
- **6,344条** CSS个股指标（营收、净利润、不良率、净息差……银行业全覆盖）
- **SQLite + FTS5** 全文索引，任意关键词搜索耗时 **1-2毫秒**
- **41个已验证指标** 经人工逐一确认代码-名称对应关系，LLM可直接使用

## "野生"插件 vs 官方插件

2025-2026年，海外金融数据商陆续官方接入LLM生态：FactSet通过MCP连接器接入Claude，S&P Global Capital IQ和LSEG通过合作插件提供数据服务。

> **FactSet走的是正门**——官方合作、标准API、完整文档。**我们走的是后门**——逆向工程、自建字典、野生插件。但推开门之后，Claude看到的数据是一样的。

本质上我们做的和FactSet做的是同一件事：把金融终端的结构化数据接入LLM，让它从"只会读文档的学者"变成"能用工具的分析师"。区别只是他们有官方支持，我们靠逆向工程。

## 文件说明

| 文件 | 大小 | 说明 |
|------|------|------|
| `build_unified_db.py` | 14KB | **核心脚本**：合并EDB+CSS字典 → 构建SQLite FTS5搜索库 + CLI搜索 |
| `export_edb_full.py` | 27KB | 导出EDB宏观指标字典（爬取东方财富在线页面，需EMQuant登录） |
| `export_css_indicators.py` | 7.5KB | 导出CSS个股指标字典（调EMQuant API获取指标定义） |
| `fetch_monetary_data.py` | 19KB | 数据拉取示例（用搜索库+已验证清单拉取宏观数据） |
| `verified_indicators.json` | 7.6KB | 41个已验证指标清单（EDB 24个 + CSS 17个） |
| `css_indicator_dict.json` | 9.4MB | CSS个股指标字典（6,344条，含调用模板） |
| `edb_emm_codes.json` | 225KB | EDB指标代码列表（138万条指标的代码索引） |

**注意**：两个大型SQLite数据库（`unified_indicator_dict.db` 761MB、`edb_indicator_dict.db` 975MB）未包含在仓库中，需本地重建（见下方说明）。

## 快速开始：搜索指标

前提：已构建好搜索库（`unified_indicator_dict.db`）。

```bash
# 搜索任意关键词
python build_unified_db.py --search GDP
python build_unified_db.py --search 不良贷款
python build_unified_db.py --search M2

# 按来源过滤
python build_unified_db.py --search ROE --source css    # 只搜个股指标
python build_unified_db.py --search 社融 --source edb   # 只搜宏观指标

# 按频率过滤（仅EDB）
python build_unified_db.py --search GDP --freq 季

# 显示全部结果
python build_unified_db.py --search GDP --top 0
```

示例输出：
```
搜索: "GDP" (来源: 全部, 频率: 不限, 显示: 15条)
共找到 412 条结果 (显示前15条):

[EDB]  EMM00000024  中国:GDP:不变价:同比            季  %     2000-03~2025-12
[EDB]  EMM00000023  中国:GDP:不变价:累计同比         季  %     1992-03~2025-12
[EDB]  EMM00000001  中国:GDP:现价:当季值            季  亿元   1992-03~2025-12
...
```

## 从零重建（完整步骤）

### 前置要求

- Python 3.8+
- 东方财富Choice量化终端（EMQuant）账号
- 需要在安装了EMQuant的Windows机器上运行

### Step 1: 导出EDB宏观指标字典

```bash
python export_edb_full.py
```

这会爬取东方财富的EDB在线查询页面，逐页解析所有宏观指标。输出：
- `edb_indicator_dict.json`（大文件，~1GB，含138万条指标详情）
- `edb_emm_codes.json`（指标代码索引）

耗时约2-3小时（受网络和反爬限制）。

### Step 2: 导出CSS个股指标字典

```bash
python export_css_indicators.py
```

调用EMQuant API获取所有CSS指标的定义和调用模板。输出 `css_indicator_dict.json`。

### Step 3: 构建统一搜索库

```bash
python build_unified_db.py
```

合并EDB和CSS字典，构建SQLite FTS5全文索引。输出 `unified_indicator_dict.db`（~761MB）。

### Step 4: 搜索测试

```bash
python build_unified_db.py --search CPI
python build_unified_db.py --search 净息差 --source css
```

## 已验证指标清单

以下指标经人工逐一确认代码与名称的对应关系，可直接使用。

### EDB 宏观指标（24个）

| 简称 | 代码 | 全称 | 频率 |
|------|------|------|------|
| 10年期国债收益率 | EMM00166466 | 中债国债到期收益率:10年 | 日 |
| 5年期国债收益率 | EMM00166462 | 中债国债到期收益率:5年 | 日 |
| 3年期国债收益率 | EMM00166460 | 中债国债到期收益率:3年 | 日 |
| 7年期国债收益率 | EMM00166464 | 中债国债到期收益率:7年 | 日 |
| 7天逆回购利率 | E1715081 | 7天逆回购操作利率 | 日 |
| 1年期MLF利率 | E1715121 | 1年期MLF投放利率 | 日 |
| LPR 1年 | E1702465 | 1年期LPR | 日 |
| LPR 5年以上 | E1707199 | 5年以上LPR | 日 |
| DR007 | E1300004 | 存款类机构质押式回购加权利率:7天 | 日 |
| SHIBOR隔夜 | EMM00166252 | 月均SHIBOR:隔夜 | 月 |
| 加权平均贷款利率(一般贷款) | E1701127 | 金融机构人民币贷款加权平均利率:一般贷款 | 季 |
| 加权平均贷款利率(总) | E1701126 | 金融机构人民币贷款加权平均利率 | 季 |
| M0同比 | EMM00087111 | 中国:M0:同比 | 月 |
| M1同比 | EMM00087113 | 中国:M1:同比 | 月 |
| M2同比 | EMM00087117 | 中国:M2:同比 | 月 |
| 社融存量同比 | EMM00634721 | 社会融资规模存量:同比 | 月 |
| CPI同比 | EMM00072301 | CPI:当月同比 | 月 |
| PPI同比 | EMM00073348 | PPI:全部工业品:当月同比 | 月 |
| GDP当季同比 | EMM00000024 | 中国:GDP:不变价:同比 | 季 |
| 商业银行净息差 | EMM00088194 | 商业银行:净息差 | 季 |
| 存款准备金率(大型) | EMM01280574 | 存款准备金率:大型金融机构(月) | 月 |
| 存款准备金率(中小型) | EMM01280575 | 存款准备金率:中小型金融机构(月) | 月 |

### CSS 个股指标（17个）

| 简称 | 代码 | 说明 |
|------|------|------|
| 营业收入 | INCOMESTATEMENT_9 | 利润表 |
| 归母净利润 | INCOMESTATEMENT_61 | 利润表 |
| 不良贷款率 | BANKSPEIND_5 | 银行专项 |
| 拨备覆盖率 | BANKSPEIND_23 | 银行专项 |
| 成本收入比 | BANKSPEIND_24 | 银行专项 |
| 资本充足率 | BANKSPEIND_31 | 银行专项 |
| 核心一级资本充足率 | BANKSPEIND_32 | 银行专项 |
| 一级资本充足率 | BANKSPEIND_37 | 银行专项 |
| 贷款拨备率 | BANKSPEIND_51 | 银行专项 |
| 流动性覆盖率 | BANKSPEIND_63 | 银行专项 |
| 存贷比 | BANKSPEIND_6 | 银行专项 |
| 净息差(公布值) | NETCARPUB | 银行专项 |
| 净息差(计算值) | NETCAR | 银行专项 |
| 净利差(公布值) | NETMARPUB | 银行专项 |
| 净利差(计算值) | NETMAR | 银行专项 |
| ROE(加权) | ROEWA | 通用 |

> **踩坑提醒**：`INCOMESTATEMENT_48` 是"营业利润"，不是"归母净利润"（`INCOMESTATEMENT_61`）。指标名相似但含义完全不同，数值差出几倍。这就是为什么我们在工作流里必须加人工确认卡点。

## 架构设计

```
用户自然语言需求
    ↓
Claude 搜索统一指标库 (build_unified_db.py)
    ↓
列出候选指标 → 人工确认 ← verified_indicators.json
    ↓
确认后调 EMQuant API 拉取数据
    ↓
生成图表 + 分析报告
```

**核心原则**：LLM搜索候选指标后，必须汇总清单给人确认。确认过的指标进入已验证清单（`verified_indicators.json`），下次直接跳过。这是防止LLM选错指标的关键安全机制。

## License

MIT

## EMQuant 凭据配置与离线检查

`fetch_monetary_data.py` 在每次登录前读取 `EMQUANT_USERNAME` 和
`EMQUANT_PASSWORD` 环境变量。请通过本地安全的运行环境注入已有账号配置；
不要将真实凭据写入源码、命令历史、日志或提交。`.env.example` 仅说明变量名，
脚本不会自动加载 `.env`。缺少配置时跳过登录并保留原有政策数据兜底。
由于 SDK 使用逗号分隔选项，凭据不能含逗号、换行或 NUL。
SDK 登录错误、异常及数据请求错误仅记录固定提示，不输出原始错误文本。

离线回归检查（不需要 EMQuant、pandas 或网络）：

```bash
python3 -m unittest discover -s tests
```

从当前代码移除凭据不会清除 Git 历史、既有克隆、缓存或日志。曾公开的凭据
应由账号持有人自行撤销或轮换。任何历史清理、强制推送及远程引用删除需另行
评估并授权，且无法保证删除他人保留的副本。
