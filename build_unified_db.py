# -*- coding:utf-8 -*-
"""
合并 EDB(宏观) + CSS(个股) 指标字典，构建统一搜索库。

数据来源（只读）:
  - edb_indicator_dict.db    (138万条EDB宏观指标)
  - css_indicator_dict.json  (6344条CSS个股指标)

输出（新文件，不修改任何现有数据库）:
  - unified_indicator_dict.db

用法:
  python build_unified_db.py                  # 构建统一库
  python build_unified_db.py --search GDP     # 统一搜索
  python build_unified_db.py --search ROE     # 搜索个股指标
  python build_unified_db.py --search 不良贷款 # 宏观+个股混合结果
  python build_unified_db.py --search M2 --source edb   # 只搜EDB
  python build_unified_db.py --search PE --source css    # 只搜CSS
  python build_unified_db.py --search GDP --freq 季      # 频率过滤(仅EDB)
  python build_unified_db.py --search GDP --top 0        # 显示全部
  python build_unified_db.py --stats                     # 查看统计
"""
import json, time, os, sys, argparse, sqlite3
sys.stdout.reconfigure(line_buffering=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EDB_DB = os.path.join(BASE_DIR, "edb_indicator_dict.db")
CSS_JSON = os.path.join(BASE_DIR, "css_indicator_dict.json")
UNIFIED_DB = os.path.join(BASE_DIR, "unified_indicator_dict.db")


def build():
    """从EDB库 + CSS JSON构建统一搜索库"""
    print("=" * 60)
    print("构建统一指标字典")
    print("=" * 60)

    if not os.path.exists(EDB_DB):
        print(f"EDB库不存在: {EDB_DB}")
        return
    if not os.path.exists(CSS_JSON):
        print(f"CSS字典不存在: {CSS_JSON}")
        return

    t0 = time.time()

    # ---- 读取EDB ----
    print("\n读取EDB库...")
    t1 = time.time()
    conn_edb = sqlite3.connect(EDB_DB)
    edb_rows = conn_edb.execute(
        "SELECT macro_id, macro_name, frequency, unit, start_date, end_date, "
        "data_source, category_path, category_id FROM indicators"
    ).fetchall()
    conn_edb.close()
    print(f"  EDB: {len(edb_rows):,} 条 ({time.time()-t1:.1f}秒)")

    # ---- 读取CSS ----
    print("读取CSS字典...")
    t1 = time.time()
    with open(CSS_JSON, 'r', encoding='utf-8') as f:
        css_all = json.load(f)
    print(f"  CSS: {len(css_all):,} 条 ({time.time()-t1:.1f}秒)")

    # ---- 构建统一库 ----
    print(f"\n构建统一库: {UNIFIED_DB}")
    if os.path.exists(UNIFIED_DB):
        os.remove(UNIFIED_DB)

    conn = sqlite3.connect(UNIFIED_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    c = conn.cursor()

    # 统一索引表
    c.execute("""CREATE TABLE unified (
        source TEXT,
        code TEXT,
        name TEXT,
        category_path TEXT,
        call_template TEXT,
        unit TEXT,
        frequency TEXT,
        start_date TEXT,
        end_date TEXT,
        params_json TEXT,
        definition TEXT
    )""")

    # ---- 插入EDB ----
    print("插入EDB数据...")
    t1 = time.time()
    edb_batch = []
    for r in edb_rows:
        macro_id, macro_name, freq, unit, start, end, source, path, cat_id = r
        template = f'c.edb("{macro_id}", "2020-01-01", "2025-12-31")'
        edb_batch.append((
            'EDB', macro_id, macro_name, path or '', template,
            unit or '', freq or '', start or '', end or '',
            '', ''  # params_json, definition
        ))

    c.executemany("INSERT INTO unified VALUES (?,?,?,?,?,?,?,?,?,?,?)", edb_batch)
    print(f"  {len(edb_batch):,} 条 ({time.time()-t1:.1f}秒)")

    # ---- 插入CSS ----
    print("插入CSS数据...")
    t1 = time.time()
    css_batch = []
    for ind in css_all:
        eng = ind['eng_name']
        name = ind['name']
        params = ind.get('params', [])
        params_json = json.dumps(params, ensure_ascii=False)

        # 生成调用模板
        param_parts = []
        for p in params:
            pname = p.get('name', '')
            default = p.get('default_value', '')
            desc = p.get('description', '')
            dtype = p.get('data_type', '')
            if 'Date' in dtype or '日期' in desc:
                param_parts.append(f'{pname}=2024-12-31')
            elif '报表类型' in desc or '报告类型' in desc:
                param_parts.append(f'{pname}=1')
            elif default and default not in ('N', 'GetLastestClosingDate'):
                param_parts.append(f'{pname}={default}')
            elif pname:
                param_parts.append(f'{pname}=...')

        param_str = ','.join(param_parts)
        if param_str:
            template = f'c.css("{{代码}}", "{eng}", "{param_str}")'
        else:
            template = f'c.css("{{代码}}", "{eng}")'

        css_batch.append((
            'CSS', eng, name, ind.get('category_path', ''), template,
            ind.get('unit', ''), '',  # frequency not applicable for CSS
            '', '',  # start_date, end_date
            params_json, ind.get('definition', '')
        ))

    c.executemany("INSERT INTO unified VALUES (?,?,?,?,?,?,?,?,?,?,?)", css_batch)
    print(f"  {len(css_batch):,} 条 ({time.time()-t1:.1f}秒)")

    # ---- FTS5全文索引 ----
    print("构建FTS5索引...")
    t1 = time.time()
    c.execute("""CREATE VIRTUAL TABLE unified_fts USING fts5(
        code, name, category_path,
        content='unified', content_rowid='rowid'
    )""")
    c.execute("INSERT INTO unified_fts(unified_fts) VALUES('rebuild')")
    print(f"  FTS5构建完成 ({time.time()-t1:.1f}秒)")

    # ---- 普通索引 ----
    print("构建普通索引...")
    c.execute("CREATE INDEX idx_name ON unified(name)")
    c.execute("CREATE INDEX idx_source ON unified(source)")
    c.execute("CREATE INDEX idx_code ON unified(code)")
    c.execute("CREATE INDEX idx_freq ON unified(frequency)")

    conn.commit()

    total = c.execute("SELECT COUNT(*) FROM unified").fetchone()[0]
    edb_count = c.execute("SELECT COUNT(*) FROM unified WHERE source='EDB'").fetchone()[0]
    css_count = c.execute("SELECT COUNT(*) FROM unified WHERE source='CSS'").fetchone()[0]
    conn.close()

    elapsed = time.time() - t0
    db_size = os.path.getsize(UNIFIED_DB) / 1024 / 1024

    print(f"\n{'='*60}")
    print(f"统一库构建完成!")
    print(f"  EDB(宏观): {edb_count:,} 条")
    print(f"  CSS(个股): {css_count:,} 条")
    print(f"  合计: {total:,} 条")
    print(f"  大小: {db_size:.0f} MB")
    print(f"  耗时: {elapsed:.0f}秒")
    print(f"  文件: {UNIFIED_DB}")
    print(f"{'='*60}")


def search(keyword, source=None, freq=None, top_n=15):
    """统一搜索"""
    if not os.path.exists(UNIFIED_DB):
        print("统一库不存在，先运行: python build_unified_db.py")
        return

    conn = sqlite3.connect(UNIFIED_DB)
    c = conn.cursor()

    t0 = time.time()

    # 构造WHERE条件
    conditions = []
    params = []

    if source:
        conditions.append("u.source = ?")
        params.append(source.upper())
    if freq:
        conditions.append("u.frequency = ?")
        params.append(freq)

    extra_where = (" AND " + " AND ".join(conditions)) if conditions else ""

    # FTS搜索
    results = c.execute(
        f"SELECT u.* FROM unified_fts f JOIN unified u ON f.rowid = u.rowid "
        f"WHERE unified_fts MATCH ?{extra_where}",
        [f'"{keyword}"'] + params
    ).fetchall()

    # FTS没结果回退LIKE
    if not results:
        like_where = "(u.name LIKE ? OR u.category_path LIKE ? OR u.code LIKE ?)"
        if conditions:
            like_where += " AND " + " AND ".join(conditions)
        results = c.execute(
            f"SELECT * FROM unified u WHERE {like_where}",
            [f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'] + params
        ).fetchall()

    elapsed = time.time() - t0
    conn.close()

    if not results:
        hints = []
        if source:
            hints.append(f"source={source}")
        if freq:
            hints.append(f"freq={freq}")
        hint = f" ({', '.join(hints)})" if hints else ""
        print(f"搜索 '{keyword}'{hint} — 未找到 ({elapsed*1000:.0f}ms)")
        return

    # 智能排序
    def rank(r):
        src, code, name = r[0], r[1], r[2]
        score = 0
        kw = keyword.lower()

        # 名称匹配 > 路径匹配
        if kw not in name.lower():
            score += 5000

        # 停止的排后面
        if '停止' in name or '(停止)' in name:
            score += 3000

        # "中国:" 开头优先（宏观全国数据）
        if src == 'EDB' and not name.startswith('中国:'):
            score += 500

        # 名字短 = 更核心
        score += len(name) * 10

        # EDB有时间跨度的，跨度长的优先
        start, end = r[7], r[8]
        try:
            sy = int(str(start)[:4]) if start else 2025
            ey = int(str(end)[:4]) if end else 2000
            score -= (ey - sy) * 2
        except (ValueError, TypeError):
            pass

        return score

    results.sort(key=rank)
    show = results if top_n == 0 else results[:top_n]

    total_found = len(results)
    filter_hint = ""
    if source:
        filter_hint += f", source={source.upper()}"
    if freq:
        filter_hint += f", freq={freq}"
    print(f"搜索 '{keyword}'{filter_hint} — {total_found}条匹配, Top {len(show)} ({elapsed*1000:.0f}ms)\n")

    for i, r in enumerate(show, 1):
        src, code, name, path, template, unit, freq_val, start, end = r[:9]
        params_json, definition = r[9], r[10]

        tag = f"[{src}]"
        parts = [name]
        if unit:
            parts.append(unit)
        if freq_val:
            parts.append(freq_val)
        if start and end:
            parts.append(f"{start}~{end}")

        print(f"  {i:2d}. {tag:5s} {' | '.join(parts)}")
        print(f"         {template}")

        # CSS显示参数说明
        if src == 'CSS' and params_json:
            try:
                plist = json.loads(params_json)
                if plist:
                    pdesc = ', '.join(f"{p['description']}" for p in plist if p.get('description'))
                    if pdesc:
                        print(f"         参数: {pdesc}")
            except:
                pass
        print()

    if total_found > len(show):
        print(f"  ... 还有 {total_found - len(show)} 条")
        print(f"  用 --top 0 查看全部, --source edb/css 过滤来源, --freq 季/月/年 过滤频率")


def stats():
    """统计信息"""
    if not os.path.exists(UNIFIED_DB):
        print("统一库不存在")
        return

    conn = sqlite3.connect(UNIFIED_DB)
    c = conn.cursor()

    total = c.execute("SELECT COUNT(*) FROM unified").fetchone()[0]
    edb = c.execute("SELECT COUNT(*) FROM unified WHERE source='EDB'").fetchone()[0]
    css = c.execute("SELECT COUNT(*) FROM unified WHERE source='CSS'").fetchone()[0]

    print(f"统一指标字典统计:")
    print(f"  总计: {total:,} 条")
    print(f"  EDB(宏观): {edb:,} 条")
    print(f"  CSS(个股): {css:,} 条")
    print(f"  库大小: {os.path.getsize(UNIFIED_DB)/1024/1024:.0f} MB")

    # CSS按大类
    print(f"\nCSS指标分布:")
    rows = c.execute(
        "SELECT substr(category_path, 1, instr(substr(category_path, instr(category_path, ' / ')+3), ' / ') "
        "+ instr(category_path, ' / ') + 1) as cat, COUNT(*) "
        "FROM unified WHERE source='CSS' GROUP BY cat ORDER BY COUNT(*) DESC LIMIT 15"
    ).fetchall()
    # 更简单的方式
    rows = c.execute("""
        SELECT CASE
            WHEN instr(category_path, ' / ') > 0 THEN
                substr(category_path, instr(category_path, ' / ')+3,
                    CASE WHEN instr(substr(category_path, instr(category_path, ' / ')+3), ' / ') > 0
                         THEN instr(substr(category_path, instr(category_path, ' / ')+3), ' / ')-1
                         ELSE length(category_path) END)
            ELSE category_path END as cat,
            COUNT(*)
        FROM unified WHERE source='CSS'
        GROUP BY cat ORDER BY COUNT(*) DESC LIMIT 15
    """).fetchall()
    for cat, count in rows:
        print(f"  {cat}: {count}")

    # EDB频率分布
    print(f"\nEDB频率分布:")
    rows = c.execute(
        "SELECT frequency, COUNT(*) FROM unified WHERE source='EDB' "
        "GROUP BY frequency ORDER BY COUNT(*) DESC LIMIT 10"
    ).fetchall()
    for freq, count in rows:
        print(f"  {freq or '未知'}: {count:,}")

    conn.close()


def main():
    parser = argparse.ArgumentParser(description="统一指标字典: EDB(宏观) + CSS(个股)")
    parser.add_argument('--search', type=str, help='搜索关键词')
    parser.add_argument('--source', type=str, choices=['edb', 'css'], help='只搜指定来源')
    parser.add_argument('--freq', type=str, help='频率过滤(仅EDB): 季/月/年/日')
    parser.add_argument('--top', type=int, default=15, help='返回条数(默认15, 0=全部)')
    parser.add_argument('--stats', action='store_true', help='查看统计')
    args = parser.parse_args()

    if args.search:
        search(args.search, source=args.source, freq=args.freq, top_n=args.top)
    elif args.stats:
        stats()
    else:
        build()


if __name__ == "__main__":
    main()
