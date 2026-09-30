# -*- coding:utf-8 -*-
"""
央行货币政策研究报告 - 数据获取脚本
功能：
1. 登录EMQuant，发现并验证EDB宏观指标代码
2. 拉取全部需要的宏观数据时序
3. 内置硬编码的政策里程碑数据作为兜底
4. 导出为 pickle + Excel
"""

import os
import sys
import json
import pickle
from datetime import datetime, timedelta

import pandas as pd
import numpy as np

# ============================================================
# 配置
# ============================================================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = BASE_DIR
EDB_MAP_FILE = os.path.join(OUTPUT_DIR, "edb_code_map.json")
DATA_PKL_FILE = os.path.join(OUTPUT_DIR, "monetary_policy_data.pkl")
DATA_XLSX_FILE = os.path.join(OUTPUT_DIR, "monetary_policy_data.xlsx")


START_DATE = "2013-01-01"
END_DATE = datetime.today().strftime("%Y-%m-%d")

# ============================================================
# 已验证的EDB代码（2026-02实测确认）
# 注意：代码经逐一验证，不要随意添加未验证代码
# ============================================================
EDB_CODES = {
    # --- 国债收益率（日度）---
    "10年期国债收益率": "EMM00166466",   # 中债国债到期收益率:10年
    "5年期国债收益率": "EMM00166462",    # 中债国债到期收益率:5年
    "3年期国债收益率": "EMM00166460",    # 中债国债到期收益率:3年
    "7年期国债收益率": "EMM00166464",    # 中债国债到期收益率:7年

    # --- 政策利率（日度）---
    "OMO逆回购利率_7天": "E1715081",    # 7天逆回购操作利率
    "MLF利率_1年": "E1715121",          # 1年期MLF投放利率
    "LPR_1年": "E1702465",             # 1年期LPR
    "LPR_5年以上": "E1707199",          # 5年以上LPR

    # --- 货币市场利率（日度）---
    "DR007": "E1300004",               # 存款类机构质押式回购加权利率:7天
    "SHIBOR_隔夜": "EMM00166252",      # 月均SHIBOR:隔夜

    # --- 贷款利率（季度）---
    "加权平均贷款利率_一般贷款": "E1701127",  # 金融机构人民币贷款加权平均利率:一般贷款
    "加权平均贷款利率_总": "E1701126",       # 金融机构人民币贷款加权平均利率

    # --- 货币供应（月度）---
    "M1_同比": "EMM00087113",           # 中国:M1:同比
    "M2_同比": "EMM00087117",           # 中国:M2:同比
    "M0_同比": "EMM00087111",           # 中国:M0:同比

    # --- 社会融资（月度）---
    "社融存量_同比": "EMM00634721",      # 社会融资规模存量:同比

    # --- 物价（月度）---
    "CPI_同比": "EMM00072301",          # CPI:当月同比
    "PPI_同比": "EMM00073348",          # PPI:全部工业品:当月同比

    # --- GDP（季度）---
    "GDP_当季同比": "EMM00000024",      # 中国:GDP:不变价:同比

    # --- 银行经营（季度）---
    "商业银行净息差": "EMM00088194",     # 商业银行:净息差

    # --- 存款准备金率（月度）---
    "存款准备金率_大型": "EMM01280574",   # 存款准备金率:大型金融机构(月)
    "存款准备金率_中小型": "EMM01280575", # 存款准备金率:中小型金融机构(月)
}

# ============================================================
# 硬编码的政策里程碑数据（央行公开信息，确保兜底）
# ============================================================
POLICY_MILESTONES = {
    "7天逆回购利率调整": [
        # (日期, 调整后利率%, 行长, 调整幅度bp)
        ("2015-10-26", 2.25, "周小川", -10),
        ("2016-02-03", 2.25, "周小川", 0),
        ("2017-02-03", 2.35, "周小川", +10),
        ("2017-03-16", 2.45, "周小川", +10),
        ("2018-03-22", 2.55, "易纲", +5),
        ("2019-11-18", 2.50, "易纲", -5),
        ("2020-02-03", 2.40, "易纲", -10),
        ("2020-03-30", 2.20, "易纲", -20),
        ("2022-01-17", 2.10, "易纲", -10),
        ("2022-08-15", 2.00, "易纲", -10),
        ("2023-06-13", 1.90, "易纲", -10),
        ("2023-08-15", 1.80, "潘功胜", -10),
        ("2024-07-22", 1.70, "潘功胜", -10),
        ("2024-09-27", 1.50, "潘功胜", -20),
        ("2025-05-08", 1.40, "潘功胜", -10),
    ],
    "1年MLF利率调整": [
        ("2019-11-05", 3.25, "易纲", -5),
        ("2020-02-17", 3.15, "易纲", -10),
        ("2020-04-15", 2.95, "易纲", -20),
        ("2022-01-17", 2.85, "易纲", -10),
        ("2022-08-15", 2.75, "易纲", -10),
        ("2023-06-15", 2.65, "易纲", -10),
        ("2023-08-15", 2.50, "潘功胜", -15),
        ("2024-07-25", 2.30, "潘功胜", -20),
        ("2024-09-25", 2.00, "潘功胜", -30),
    ],
    "LPR_1年调整": [
        ("2019-08-20", 4.25, "易纲", None),  # LPR改革首次报价
        ("2019-09-20", 4.20, "易纲", -5),
        ("2019-11-20", 4.15, "易纲", -5),
        ("2020-02-20", 4.05, "易纲", -10),
        ("2020-04-20", 3.85, "易纲", -20),
        ("2021-12-20", 3.80, "易纲", -5),
        ("2022-01-20", 3.70, "易纲", -10),
        ("2022-08-22", 3.65, "易纲", -5),
        ("2023-06-20", 3.55, "易纲", -10),
        ("2023-08-21", 3.45, "潘功胜", -10),
        ("2024-07-22", 3.35, "潘功胜", -10),
        ("2024-10-21", 3.10, "潘功胜", -25),
        ("2025-05-20", 3.00, "潘功胜", -10),
    ],
    "LPR_5年以上调整": [
        ("2019-08-20", 4.85, "易纲", None),
        ("2019-11-20", 4.80, "易纲", -5),
        ("2020-02-20", 4.75, "易纲", -5),
        ("2020-04-20", 4.65, "易纲", -10),
        ("2022-01-20", 4.60, "易纲", -5),
        ("2022-05-20", 4.45, "易纲", -15),
        ("2022-08-22", 4.30, "易纲", -15),
        ("2023-06-20", 4.20, "易纲", -10),
        ("2024-02-20", 3.95, "潘功胜", -25),
        ("2024-07-22", 3.85, "潘功胜", -10),
        ("2024-10-21", 3.60, "潘功胜", -25),
        ("2025-05-20", 3.50, "潘功胜", -10),
    ],
    "存款准备金率调整_大型": [
        # 只列2018年以来主要调整
        ("2018-04-25", 16.00, "易纲", -100),
        ("2018-07-05", 15.50, "易纲", -50),
        ("2018-10-15", 14.50, "易纲", -100),
        ("2019-01-15", 13.50, "易纲", -50),  # 分两次各50bp
        ("2019-01-25", 13.00, "易纲", -50),
        ("2019-05-15", 12.50, "易纲", -50),  # 中小行
        ("2019-09-16", 13.00, "易纲", -50),
        ("2020-01-06", 12.50, "易纲", -50),
        ("2020-03-16", 12.50, "易纲", 0),  # 定向降准
        ("2020-04-15", 11.50, "易纲", -100),  # 分两次
        ("2021-07-15", 12.00, "易纲", -50),
        ("2021-12-15", 11.50, "易纲", -50),
        ("2022-04-25", 11.25, "易纲", -25),
        ("2022-12-05", 11.00, "易纲", -25),
        ("2023-03-27", 10.75, "易纲", -25),
        ("2023-09-15", 10.50, "潘功胜", -25),
        ("2024-02-05", 10.00, "潘功胜", -50),
        ("2024-09-27", 9.50, "潘功胜", -50),
        ("2025-05-15", 9.00, "潘功胜", -50),
    ],
    "商业银行净息差_季度": [
        # (日期, NIM%)
        ("2017-12-31", 2.10),
        ("2018-03-31", 2.08),
        ("2018-06-30", 2.12),
        ("2018-09-30", 2.15),
        ("2018-12-31", 2.18),
        ("2019-03-31", 2.17),
        ("2019-06-30", 2.18),
        ("2019-09-30", 2.19),
        ("2019-12-31", 2.20),
        ("2020-03-31", 2.10),
        ("2020-06-30", 2.09),
        ("2020-09-30", 2.09),
        ("2020-12-31", 2.10),
        ("2021-03-31", 2.07),
        ("2021-06-30", 2.06),
        ("2021-09-30", 2.06),
        ("2021-12-31", 2.08),
        ("2022-03-31", 1.97),
        ("2022-06-30", 1.94),
        ("2022-09-30", 1.94),
        ("2022-12-31", 1.91),
        ("2023-03-31", 1.74),
        ("2023-06-30", 1.74),
        ("2023-09-30", 1.73),
        ("2023-12-31", 1.69),
        ("2024-03-31", 1.54),
        ("2024-06-30", 1.54),
        ("2024-09-30", 1.53),
        ("2024-12-31", 1.52),
    ],
    "存款利率改革大事记": [
        # (日期, 事件, 类型)
        ("2022-04-01", "建立存款利率市场化调整机制", "制度"),
        ("2022-09-15", "多家银行下调存款挂牌利率（首轮）", "降息"),
        ("2023-06-08", "活期0.20%(-5bp)，定期各期限下调", "降息"),
        ("2023-09-01", "1Y/2Y/3Y/5Y下调10/20/25/25bp", "降息"),
        ("2023-12-22", "第四轮存款利率下调，10-25bp", "降息"),
        ("2024-04-01", "禁止手工补息（里程碑式改革）", "制度"),
        ("2024-07-25", "大幅下调：各期限降至1.35%/1.45%/1.75%/1.80%", "降息"),
        ("2024-10-18", "全面下调25bp：1Y=1.10%，进入1%时代", "降息"),
    ],
    "央行创新工具": [
        # (日期, 工具名称, 初始规模亿元, 简述)
        ("2024-08-01", "国债买卖重启", 1000, "近20年来首次买卖国债，净买入1000亿"),
        ("2024-09-24", "证券基金保险公司互换便利(SFISF)", 5000, "机构用债券/ETF换国债央票，增持股票"),
        ("2024-09-24", "股票回购增持再贷款", 3000, "1.75%再贷款引导银行支持上市公司回购增持"),
        ("2024-10-28", "买断式逆回购", None, "3个月-1年期限，利率招标，逐步替代MLF"),
        ("2025-05-07", "服务消费与养老再贷款", 5000, "引导银行增加服务消费和养老信贷支持"),
        ("2025-05-07", "科技创新债券风险分担工具", None, "再贷款购买科创债+地方政府风险分担"),
    ],
}

# 行长任期
GOVERNOR_PERIODS = {
    "周小川": ("2002-12-01", "2018-03-18"),
    "易纲": ("2018-03-19", "2023-07-24"),
    "潘功胜": ("2023-07-25", "2026-12-31"),
}


# ============================================================
# EMQuant 数据获取
# ============================================================
def login_emquant():
    """登录EMQuant"""
    username = os.environ.get("EMQUANT_USERNAME", "")
    password = os.environ.get("EMQUANT_PASSWORD", "")
    if not username.strip() or not password.strip():
        print("[EMQuant] 请设置 EMQUANT_USERNAME 和 EMQUANT_PASSWORD")
        return False
    if any(char in value for value in (username, password) for char in (",", "\n", "\r", "\x00")):
        print("[EMQuant] 凭据含不支持的选项分隔符")
        return False
    from EmQuantAPI import c
    def mainCallback(quantdata):
        if str(quantdata.ErrorCode) in ("10001011", "10001009"):
            print("[EMQuant] 账号掉线")

    options = f"ForceLogin=1,UserName={username},Password={password}"
    try:
        result = c.start(options, '', mainCallback)
    except Exception:
        print("[EMQuant] 登录异常，请在本地检查SDK和账号配置")
        return False
    if result.ErrorCode != 0:
        print("[EMQuant] 登录失败，请在本地检查账号配置")
        return False
    # 修复：Windows ACP=65001(UTF-8)时，SDK默认用gbk解码会出错
    c.EncodeType = 'utf-8'
    print("[EMQuant] 登录成功（已设置UTF-8编码）")
    return True


def fetch_all_edb_data():
    """拉取所有已确认的EDB时序数据"""
    from EmQuantAPI import c

    datasets = {}
    print("\n=== 拉取EDB时序数据 ===")

    for name, code in EDB_CODES.items():
        try:
            data = c.edb(code, f"StartDate={START_DATE},EndDate={END_DATE},Ispandas=0")
            if isinstance(data, c.EmQuantData) and data.ErrorCode == 0:
                dates = []
                values = []
                for cd in data.Codes:
                    if cd in data.Data and len(data.Data[cd]) > 0:
                        for j in range(len(data.Dates)):
                            dates.append(str(data.Dates[j]))
                            values.append(data.Data[cd][0][j])

                if dates:
                    df = pd.DataFrame({"date": pd.to_datetime(dates), "value": pd.to_numeric(values, errors='coerce')})
                    df = df.dropna(subset=["value"])
                    df = df.sort_values("date").reset_index(drop=True)
                    datasets[name] = df
                    print(f"  [OK] {name} ({code}): {len(df)}条数据 ({df['date'].min().strftime('%Y-%m')} ~ {df['date'].max().strftime('%Y-%m')})")
                else:
                    print(f"  [--] {name} ({code}): 无数据")
            else:
                print(f"  [--] {name} ({code}): 请求失败")
        except Exception as e:
            print(f"  [ERR] {name} ({code}): 请求异常")

    return datasets


def build_milestone_dataframes():
    """将硬编码的里程碑数据转为DataFrame"""
    milestone_dfs = {}

    # 利率调整类
    for key in ["7天逆回购利率调整", "1年MLF利率调整", "LPR_1年调整", "LPR_5年以上调整"]:
        records = POLICY_MILESTONES[key]
        df = pd.DataFrame(records, columns=["date", "rate", "governor", "change_bp"])
        df["date"] = pd.to_datetime(df["date"])
        milestone_dfs[key] = df

    # 存款准备金率
    records = POLICY_MILESTONES["存款准备金率调整_大型"]
    df = pd.DataFrame(records, columns=["date", "rate", "governor", "change_bp"])
    df["date"] = pd.to_datetime(df["date"])
    milestone_dfs["存款准备金率调整_大型"] = df

    # 净息差
    records = POLICY_MILESTONES["商业银行净息差_季度"]
    df = pd.DataFrame(records, columns=["date", "nim"])
    df["date"] = pd.to_datetime(df["date"])
    milestone_dfs["商业银行净息差_季度"] = df

    # 存款利率改革大事记
    records = POLICY_MILESTONES["存款利率改革大事记"]
    df = pd.DataFrame(records, columns=["date", "event", "type"])
    df["date"] = pd.to_datetime(df["date"])
    milestone_dfs["存款利率改革大事记"] = df

    # 创新工具
    records = POLICY_MILESTONES["央行创新工具"]
    df = pd.DataFrame(records, columns=["date", "tool_name", "initial_scale_yi", "description"])
    df["date"] = pd.to_datetime(df["date"])
    milestone_dfs["央行创新工具"] = df

    # 行长任期
    governor_df = pd.DataFrame([
        {"governor": name, "start": pd.to_datetime(period[0]), "end": pd.to_datetime(period[1])}
        for name, period in GOVERNOR_PERIODS.items()
    ])
    milestone_dfs["行长任期"] = governor_df

    return milestone_dfs


def build_comparison_data():
    """构建易纲vs潘功胜对比数据"""
    comparison = {
        "指标": [
            "7天逆回购累计降幅(bp)", "1年MLF累计降幅(bp)", "1年LPR累计降幅(bp)",
            "5年+LPR累计降幅(bp)", "降准累计幅度(bp)", "降准次数",
            "任期长度(月)", "创新工具数量"
        ],
        "易纲(2018.3-2023.7)": [
            -65,   # 2.55→1.90
            -60,   # 3.25→2.65（从MLF创设后开始算）
            -70,   # 4.25→3.55
            -65,   # 4.85→4.20
            -325,  # 多次降准累计
            11,    # 约11次
            64,    # 约64个月
            2,     # LPR改革、碳减排支持工具
        ],
        "潘功胜(2023.7-至今)": [
            -50,   # 1.90→1.40
            -65,   # 2.65→2.00
            -55,   # 3.55→3.00
            -70,   # 4.20→3.50
            -175,  # 4次降准
            4,     # 4次
            31,    # 约31个月（至2026.2）
            6,     # 买断式逆回购、国债买卖、SFISF、回购再贷款、服务消费再贷款、科创债工具
        ],
    }
    return pd.DataFrame(comparison)


# ============================================================
# 主流程
# ============================================================
def main():
    print("=" * 60)
    print("  央行货币政策研究报告 - 数据获取")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    all_data = {}
    emquant_available = False

    # Step 1: 尝试EMQuant登录和数据获取
    try:
        if login_emquant():
            emquant_available = True

            # 直接拉取已确认的EDB时序数据
            edb_data = fetch_all_edb_data()
            all_data["edb"] = edb_data

            # 退出
            from EmQuantAPI import c
            c.stop()
            print("\n[EMQuant] 已退出登录")
    except ImportError:
        print("\n[警告] EMQuantAPI 未安装，将仅使用硬编码数据")
    except Exception as e:
        print("\n[警告] EMQuant出错，将使用硬编码数据")
        try:
            from EmQuantAPI import c
            c.stop()
        except:
            pass

    # Step 2: 构建里程碑数据（始终执行，这是兜底）
    print("\n=== 构建政策里程碑数据 ===")
    milestone_dfs = build_milestone_dataframes()
    all_data["milestones"] = milestone_dfs
    print(f"  已构建 {len(milestone_dfs)} 个里程碑数据集")

    # Step 3: 构建对比数据
    print("\n=== 构建易纲vs潘功胜对比数据 ===")
    comparison_df = build_comparison_data()
    all_data["comparison"] = comparison_df
    print(comparison_df.to_string(index=False))

    # Step 4: 保存
    print(f"\n=== 保存数据 ===")
    with open(DATA_PKL_FILE, 'wb') as f:
        pickle.dump(all_data, f)
    print(f"  pickle: {DATA_PKL_FILE}")

    # 同时保存Excel供人工查看
    try:
        with pd.ExcelWriter(DATA_XLSX_FILE, engine='openpyxl') as writer:
            # 里程碑数据
            for name, df in milestone_dfs.items():
                sheet_name = name[:31]  # Excel sheet名最长31字符
                df.to_excel(writer, sheet_name=sheet_name, index=False)

            # 对比数据
            comparison_df.to_excel(writer, sheet_name="易纲vs潘功胜", index=False)

            # EDB数据
            if "edb" in all_data:
                for name, df in all_data["edb"].items():
                    sheet_name = f"EDB_{name}"[:31]
                    df.to_excel(writer, sheet_name=sheet_name, index=False)

        print(f"  Excel: {DATA_XLSX_FILE}")
    except Exception as e:
        print(f"  Excel保存失败: {e}")

    # 汇总
    print("\n" + "=" * 60)
    edb_count = len(all_data.get("edb", {}))
    milestone_count = len(milestone_dfs)
    print(f"  数据获取完成!")
    print(f"  EMQuant时序数据: {edb_count}个指标")
    print(f"  政策里程碑数据: {milestone_count}个数据集")
    print(f"  硬编码兜底: 全部关键政策数据已内置")
    if not emquant_available:
        print(f"  [注意] EMQuant未成功连接，报告将完全基于硬编码数据生成")
    print("=" * 60)


if __name__ == "__main__":
    main()
