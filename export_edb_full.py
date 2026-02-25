# -*- coding:utf-8 -*-
"""
全量导出东方财富Choice EDB宏观指标字典（并发版）。

从浏览器提取cookies后，用requests+线程池并行递归遍历多个子分类。
支持增量保存和断点续传。

用法:
    python export_edb_full.py                  # 导出（中国宏观+利率，8线程并发）
    python export_edb_full.py --workers 16     # 16线程并发
    python export_edb_full.py --all            # 导出全部5个大类
    python export_edb_full.py --categories 0002  # 只导出行业经济数据
    python export_edb_full.py --search PPI     # 本地搜索
    python export_edb_full.py --stats          # 查看统计
"""
import json, csv, time, os, sys, argparse, threading
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout.reconfigure(line_buffering=True)

try:
    import requests
except ImportError:
    print("需要安装requests: pip install requests")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, "browser_profile")
EDB_URL = "https://quantapi.eastmoney.com/Cmd/EconomicDataBase?from=web"
API_BASE = "https://quantapi.eastmoney.com/Command/Edb"

OUTPUT_JSON = os.path.join(BASE_DIR, "edb_indicator_dict.json")
OUTPUT_CSV = os.path.join(BASE_DIR, "edb_indicator_dict.csv")
OUTPUT_DB = os.path.join(BASE_DIR, "edb_indicator_dict.db")
PROGRESS_FILE = os.path.join(BASE_DIR, "edb_export_progress.json")

TOP_CATEGORIES = {
    "0001": "中国宏观数据",
    "1": "利率走势分析",
    "0002": "行业经济数据",
    "0003": "全球宏观数据",
    "0023": "地区宏观数据",
}
DEFAULT_CATEGORIES = ["0001", "1"]

# 线程安全的打印和保存
_print_lock = threading.Lock()
_save_lock = threading.Lock()


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def load_existing():
    if os.path.exists(OUTPUT_JSON):
        with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
            return json.load(f)
    return []


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {"completed_categories": []}


def save_progress(progress):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


def save_results(indicators):
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(indicators, f, ensure_ascii=False, indent=2)
    if indicators:
        fields = ["macro_id", "macro_name", "frequency", "unit",
                  "start_date", "end_date", "data_source", "category_path"]
        with open(OUTPUT_CSV, 'w', encoding='utf-8-sig', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
            writer.writeheader()
            writer.writerows(indicators)


def get_browser_cookies():
    """启动浏览器获取登录cookies，然后关闭浏览器"""
    from playwright.sync_api import sync_playwright

    log("启动浏览器获取cookies...")
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            USER_DATA_DIR, headless=False,
            viewport={"width": 1400, "height": 900}, locale="zh-CN",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(EDB_URL, wait_until="networkidle", timeout=30000)
        time.sleep(2)

        if "Login" in page.url:
            log("登录态失效!")
            context.close()
            return None

        # 提取cookies
        cookies = context.cookies()
        log(f"获取到 {len(cookies)} 个cookies")

        context.close()

    # 转换为requests的cookies格式
    cookie_dict = {}
    for c in cookies:
        cookie_dict[c['name']] = c['value']
    return cookie_dict


def make_session(cookies):
    """创建requests会话"""
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Referer': EDB_URL,
    })
    return session


def fetch_children(session, cat_id):
    """调用API获取子节点"""
    url = f"{API_BASE}/GetLeafNodeOnIndicatorCategoryInfoBy?id={cat_id}"
    try:
        resp = session.get(url, timeout=30)
        if resp.ok:
            data = resp.json()
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def export_subcategory(session, cat_id, cat_name, parent_path, flush_cb=None):
    """递归导出单个子分类（在独立线程中运行）。
    flush_cb: 可选回调，定期把已采集数据flush到主列表防止丢失。
    """
    indicators = []
    visited = set()
    api_calls = 0
    _last_flush = [0]  # 上次flush时的指标数

    def traverse(cid, path, depth=0):
        nonlocal api_calls
        if cid in visited:
            return
        visited.add(cid)
        children = fetch_children(session, cid)
        api_calls += 1

        if api_calls % 100 == 0:
            log(f"      [{cat_name}] API={api_calls}, 指标={len(indicators)}")

        # 每500次API调用中间保存一次，防止大分类中断丢数据
        if flush_cb and api_calls % 500 == 0 and len(indicators) > _last_flush[0]:
            batch = indicators[_last_flush[0]:]
            _last_flush[0] = len(indicators)
            flush_cb(batch)
            log(f"      [{cat_name}] 中间保存 {len(batch)} 条 (累计{len(indicators)})")

        for child in children:
            entity = child.get('Entity')
            name = child.get('name', '')
            child_id = child.get('id', '')
            is_parent = child.get('isParent', False)

            if entity:
                indicators.append({
                    "macro_id": entity.get('MacroId', ''),
                    "macro_name": entity.get('MacroName', ''),
                    "frequency": entity.get('UpdateFrequency', ''),
                    "unit": entity.get('Unit', ''),
                    "start_date": entity.get('StartDate', ''),
                    "end_date": entity.get('EndDate', ''),
                    "data_source": entity.get('DataSource', ''),
                    "category_path": path,
                    "category_id": cid,
                })
            elif is_parent and depth < 8:
                sub_path = f"{path}/{name}" if path else name
                traverse(child_id, sub_path, depth + 1)

    full_path = f"{parent_path}/{cat_name}" if parent_path else cat_name
    traverse(cat_id, full_path, depth=0)
    # 返回未flush的剩余部分
    remaining = indicators[_last_flush[0]:]
    return remaining, api_calls, _last_flush[0]


def export_tree_concurrent(cookies, categories, max_workers=8, extra_subcats=None):
    """并发导出所有子分类。extra_subcats: 直接指定的子分类ID列表"""
    all_indicators = load_existing()
    progress = load_progress()
    completed = set(progress.get("completed_categories", []))
    total_api_calls = 0

    if all_indicators:
        log(f"已有本地数据: {len(all_indicators)} 个指标")
        log(f"已完成分类: {len(completed)} 个\n")

    # 收集所有需要处理的子分类任务
    tasks = []  # (sub_id, sub_name, cat_name, sub_key)
    session_main = make_session(cookies)

    for cat_id in categories:
        cat_name = TOP_CATEGORIES.get(cat_id, cat_id)
        log(f"{'='*60}")
        log(f"顶层分类: {cat_name} (id={cat_id})")
        log(f"{'='*60}")

        sub_cats = fetch_children(session_main, cat_id)
        total_api_calls += 1
        log(f"  一级子分类: {len(sub_cats)} 个")

        for sub in sub_cats:
            sub_name = sub.get('name', '')
            sub_id = sub.get('id', '')
            is_parent = sub.get('isParent', False)
            entity = sub.get('Entity')
            sub_key = f"{cat_id}/{sub_id}"

            if sub_key in completed:
                log(f"  ✓ {sub_name} -- 已完成，跳过")
                continue

            if entity:
                all_indicators.append({
                    "macro_id": entity.get('MacroId', ''),
                    "macro_name": entity.get('MacroName', ''),
                    "frequency": entity.get('UpdateFrequency', ''),
                    "unit": entity.get('Unit', ''),
                    "start_date": entity.get('StartDate', ''),
                    "end_date": entity.get('EndDate', ''),
                    "data_source": entity.get('DataSource', ''),
                    "category_path": cat_name,
                    "category_id": cat_id,
                })
                completed.add(sub_key)
                continue

            if not is_parent:
                completed.add(sub_key)
                continue

            # 对大子分类展开为二级子节点并发
            sub_children = fetch_children(session_main, sub_id)
            total_api_calls += 1
            expanded = 0
            for sc in sub_children:
                sc_id = sc.get('id', '')
                sc_name_inner = sc.get('name', '')
                sc_is_parent = sc.get('isParent', False)
                sc_entity = sc.get('Entity')
                sc_key = f"{cat_id}/{sub_id}/{sc_id}"

                if sc_key in completed:
                    continue

                if sc_entity:
                    all_indicators.append({
                        "macro_id": sc_entity.get('MacroId', ''),
                        "macro_name": sc_entity.get('MacroName', ''),
                        "frequency": sc_entity.get('UpdateFrequency', ''),
                        "unit": sc_entity.get('Unit', ''),
                        "start_date": sc_entity.get('StartDate', ''),
                        "end_date": sc_entity.get('EndDate', ''),
                        "data_source": sc_entity.get('DataSource', ''),
                        "category_path": f"{cat_name}/{sub_name}",
                        "category_id": sub_id,
                    })
                    completed.add(sc_key)
                    continue

                if sc_is_parent:
                    tasks.append((sc_id, f"{sub_name}/{sc_name_inner}",
                                  f"{cat_name}/{sub_name}", sc_key))
                    expanded += 1
                else:
                    completed.add(sc_key)

            log(f"  → {sub_name}: 展开为 {expanded} 个二级子节点并发")

    # 处理直接指定的子分类ID — 展开为二级子节点以充分并发
    if extra_subcats:
        log(f"\n{'='*60}")
        log(f"直接指定的子分类(展开为二级子节点):")
        log(f"{'='*60}")
        for sc_id in extra_subcats:
            sc_key = f"sub/{sc_id}"
            if sc_key in completed:
                log(f"  ✓ {sc_id} -- 已完成，跳过")
                continue
            # 获取名字
            sc_name = sc_id
            parent_id = sc_id[:4]
            parent_children = fetch_children(session_main, parent_id)
            total_api_calls += 1
            for pc in parent_children:
                if pc.get('id') == sc_id:
                    sc_name = pc.get('name', sc_id)
                    break
            # 展开二级子节点
            sub_children = fetch_children(session_main, sc_id)
            total_api_calls += 1
            sub_count = 0
            for child in sub_children:
                child_id = child.get('id', '')
                child_name = child.get('name', '')
                is_parent = child.get('isParent', False)
                entity = child.get('Entity')
                child_key = f"sub/{sc_id}/{child_id}"

                if child_key in completed:
                    log(f"    ✓ {child_name} -- 已完成，跳过")
                    continue

                if entity:
                    all_indicators.append({
                        "macro_id": entity.get('MacroId', ''),
                        "macro_name": entity.get('MacroName', ''),
                        "frequency": entity.get('UpdateFrequency', ''),
                        "unit": entity.get('Unit', ''),
                        "start_date": entity.get('StartDate', ''),
                        "end_date": entity.get('EndDate', ''),
                        "data_source": entity.get('DataSource', ''),
                        "category_path": f"行业经济数据/{sc_name}",
                        "category_id": sc_id,
                    })
                    completed.add(child_key)
                    continue

                if is_parent:
                    tasks.append((child_id, f"{sc_name}/{child_name}",
                                  f"行业经济数据/{sc_name}", child_key))
                    sub_count += 1
                else:
                    completed.add(child_key)

            log(f"  + {sc_name}: 展开为 {sub_count} 个二级子节点并发")

    if not tasks:
        log("\n所有分类已完成!")
        return all_indicators

    log(f"\n待处理子分类: {len(tasks)} 个")
    log(f"并发线程数: {max_workers}")
    log(f"{'='*60}\n")

    # 并发处理
    done_count = 0

    def flush_to_main(batch):
        """中间保存回调：线程安全地把一批数据存到主列表和磁盘"""
        with _save_lock:
            all_indicators.extend(batch)
            save_results(all_indicators)

    def process_one(task_info):
        sub_id, sub_name, cat_name, sub_key = task_info
        # 每个线程用自己的session
        session = make_session(cookies)
        start_time = time.time()
        remaining, api_calls, flushed_count = export_subcategory(
            session, sub_id, sub_name, cat_name, flush_cb=flush_to_main
        )
        elapsed = time.time() - start_time
        return sub_key, sub_name, remaining, api_calls, elapsed, flushed_count

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_one, t): t for t in tasks}

        for future in as_completed(futures):
            sub_key, sub_name, remaining, api_calls, elapsed, flushed_count = future.result()
            done_count += 1
            total_api_calls += api_calls
            total_indicators = flushed_count + len(remaining)

            with _save_lock:
                all_indicators.extend(remaining)
                completed.add(sub_key)

                log(f"  [{done_count}/{len(tasks)}] {sub_name}: "
                    f"{total_indicators} 指标, {api_calls} API, {elapsed:.0f}秒  "
                    f"(累计: {len(all_indicators)})")

                # 增量保存
                save_results(all_indicators)
                progress["completed_categories"] = list(completed)
                save_progress(progress)

    log(f"\n总计API调用: {total_api_calls}")
    log(f"总计指标数: {len(all_indicators)}")
    unique_ids = set(ind['macro_id'] for ind in all_indicators if ind['macro_id'])
    log(f"去重后唯一MacroId: {len(unique_ids)}")

    return all_indicators


def build_sqlite_db():
    """从JSON构建SQLite数据库+FTS5全文索引"""
    import sqlite3
    if not os.path.exists(OUTPUT_JSON):
        log("JSON文件不存在，请先导出")
        return
    log("正在构建SQLite数据库...")
    t0 = time.time()
    with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
        indicators = json.load(f)
    log(f"  加载JSON: {len(indicators)} 条 ({time.time()-t0:.1f}秒)")

    # 去重
    seen = set()
    unique = []
    for ind in indicators:
        mid = ind.get('macro_id', '')
        if mid and mid not in seen:
            seen.add(mid)
            unique.append(ind)
    log(f"  去重后: {len(unique)} 条")

    conn = sqlite3.connect(OUTPUT_DB)
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS indicators")
    c.execute("DROP TABLE IF EXISTS indicators_fts")
    c.execute("""CREATE TABLE indicators (
        macro_id TEXT PRIMARY KEY,
        macro_name TEXT,
        frequency TEXT,
        unit TEXT,
        start_date TEXT,
        end_date TEXT,
        data_source TEXT,
        category_path TEXT,
        category_id TEXT
    )""")
    c.execute("""CREATE VIRTUAL TABLE indicators_fts USING fts5(
        macro_id, macro_name, category_path,
        content='indicators', content_rowid='rowid'
    )""")

    # 批量插入
    rows = [(ind.get('macro_id',''), ind.get('macro_name',''),
             ind.get('frequency',''), ind.get('unit',''),
             ind.get('start_date',''), ind.get('end_date',''),
             ind.get('data_source',''), ind.get('category_path',''),
             ind.get('category_id','')) for ind in unique]
    c.executemany("INSERT OR IGNORE INTO indicators VALUES (?,?,?,?,?,?,?,?,?)", rows)

    # 构建FTS索引
    c.execute("INSERT INTO indicators_fts(indicators_fts) VALUES('rebuild')")

    # 普通索引
    c.execute("CREATE INDEX IF NOT EXISTS idx_name ON indicators(macro_name)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_path ON indicators(category_path)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_freq ON indicators(frequency)")

    conn.commit()
    conn.close()
    elapsed = time.time() - t0
    db_size = os.path.getsize(OUTPUT_DB) / 1024 / 1024
    log(f"  完成! {OUTPUT_DB} ({db_size:.0f} MB, {elapsed:.1f}秒)")


def _rank_indicator(ind, keyword):
    """给指标打相关性分数，分数越小越靠前。
    排序逻辑：
      1. 名称里包含关键词 > 只在路径里包含
      2. 在用指标 > 已停止
      3. 名字越短 = 越宏观 = 越核心（"中国:GDP:现价" 优于 "中国:GDP:现价:第三产业:房地产业:累计值"）
      4. 数据时间跨度越长越好
      5. "中国:" 开头的全国数据优先于地区数据
    """
    name = ind['macro_name'] if isinstance(ind, dict) else ind[1]
    path = (ind.get('category_path', '') if isinstance(ind, dict)
            else (ind[7] if len(ind) > 7 else ''))

    score = 0
    kw = keyword.lower()
    name_lower = name.lower()

    # 关键词在名称中 vs 只在路径中
    if kw not in name_lower:
        score += 5000

    # 已停止的指标排后面
    if '(停止)' in name or '停止' in name:
        score += 3000

    # "中国:" 开头的全国数据优先
    if not name.startswith('中国:'):
        score += 1000

    # 名字长度：越短越宏观
    score += len(name) * 10

    # 数据跨度：年份越长越好 (用end_date - start_date粗略估算)
    try:
        start = ind['start_date'] if isinstance(ind, dict) else ind[4]
        end = ind['end_date'] if isinstance(ind, dict) else ind[5]
        start_y = int(str(start)[:4]) if start else 2025
        end_y = int(str(end)[:4]) if end else 2000
        span = end_y - start_y
        score -= span * 2  # 跨度越长，分数越低（越靠前）
    except (ValueError, TypeError):
        pass

    return score


def local_search(keyword, freq=None, top_n=15):
    """智能搜索指标：过滤+排序，返回最相关的Top N条。
    freq: 频率过滤 (季/月/年/日)
    top_n: 返回条数 (0=全部)
    """
    import sqlite3

    # 优先SQLite
    if os.path.exists(OUTPUT_DB):
        t0 = time.time()
        conn = sqlite3.connect(OUTPUT_DB)
        c = conn.cursor()

        total = c.execute("SELECT COUNT(*) FROM indicators").fetchone()[0]

        # 构造查询：FTS + 可选频率过滤
        t1 = time.time()
        if freq:
            # 带频率过滤的查询
            results = c.execute(
                "SELECT i.* FROM indicators_fts f JOIN indicators i ON f.rowid = i.rowid "
                "WHERE indicators_fts MATCH ? AND i.frequency = ?",
                (f'"{keyword}"', freq)
            ).fetchall()
            if not results:
                results = c.execute(
                    "SELECT * FROM indicators WHERE (macro_name LIKE ? OR category_path LIKE ?) "
                    "AND frequency = ?",
                    (f'%{keyword}%', f'%{keyword}%', freq)
                ).fetchall()
        else:
            results = c.execute(
                "SELECT i.* FROM indicators_fts f JOIN indicators i ON f.rowid = i.rowid "
                "WHERE indicators_fts MATCH ?",
                (f'"{keyword}"',)
            ).fetchall()
            if not results:
                results = c.execute(
                    "SELECT * FROM indicators WHERE macro_name LIKE ? OR category_path LIKE ?",
                    (f'%{keyword}%', f'%{keyword}%')
                ).fetchall()

        elapsed_search = time.time() - t1
        conn.close()

        total_found = len(results)
        if not results:
            log(f"搜索 '{keyword}' — 未找到匹配 ({elapsed_search*1000:.0f}ms)")
            return

        # 智能排序
        results.sort(key=lambda r: _rank_indicator(
            {'macro_name': r[1], 'macro_id': r[0], 'start_date': r[4],
             'end_date': r[5], 'category_path': r[7] if len(r) > 7 else ''},
            keyword))

        # 截取Top N
        show_results = results if top_n == 0 else results[:top_n]

        freq_hint = f", freq={freq}" if freq else ""
        log(f"搜索 '{keyword}'{freq_hint} — {total_found}条匹配, 展示Top {len(show_results)} ({elapsed_search*1000:.0f}ms)\n")

        for i, r in enumerate(show_results, 1):
            mid, mname, mfreq, unit = r[0], r[1], r[2] or '', r[3] or ''
            start, end = r[4] or '', r[5] or ''
            stopped = ' [停]' if '停止' in mname else ''
            log(f"  {i:2d}. {mid:16s} | {mname} | {mfreq} | {unit} | {start}~{end}{stopped}")

        if total_found > len(show_results):
            log(f"\n  ... 还有 {total_found - len(show_results)} 条，用 --top 0 查看全部，--freq 季/月/年 过滤")
        return

    # 回退: JSON (慢)
    if not os.path.exists(OUTPUT_JSON):
        log("本地字典不存在，请先运行导出")
        return
    log("提示: 运行 --build-db 构建SQLite索引可大幅加速搜索")
    t0 = time.time()
    with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
        indicators = json.load(f)
    log(f"本地字典: {len(indicators)} 个指标 (加载{time.time()-t0:.1f}秒)")

    kw_lower = keyword.lower()
    results = [ind for ind in indicators
               if kw_lower in ind.get('macro_name', '').lower()
               or kw_lower in ind.get('category_path', '').lower()]
    if freq:
        results = [r for r in results if r.get('frequency') == freq]

    if not results:
        log(f"搜索 '{keyword}' — 未找到匹配")
        return

    results.sort(key=lambda r: _rank_indicator(r, keyword))
    show_results = results if top_n == 0 else results[:top_n]
    log(f"搜索 '{keyword}' — {len(results)}条匹配, 展示Top {len(show_results)}\n")
    for i, item in enumerate(show_results, 1):
        mid = item['macro_id']
        mname = item['macro_name']
        mfreq = item.get('frequency', '')
        unit = item.get('unit', '')
        start = item.get('start_date', '')
        end = item.get('end_date', '')
        log(f"  {i:2d}. {mid:16s} | {mname} | {mfreq} | {unit} | {start}~{end}")
    if len(results) > len(show_results):
        log(f"\n  ... 还有 {len(results) - len(show_results)} 条")


def show_stats():
    if not os.path.exists(OUTPUT_JSON):
        log("本地字典不存在")
        return
    with open(OUTPUT_JSON, 'r', encoding='utf-8') as f:
        indicators = json.load(f)

    log(f"本地字典统计:")
    log(f"  总指标数: {len(indicators)}")
    unique_ids = set(ind['macro_id'] for ind in indicators if ind['macro_id'])
    log(f"  唯一MacroId: {len(unique_ids)}")

    by_top = {}
    for ind in indicators:
        path = ind.get('category_path', '')
        top = path.split('/')[0] if '/' in path else path
        by_top[top] = by_top.get(top, 0) + 1
    log(f"\n  按顶层分类:")
    for top, count in sorted(by_top.items(), key=lambda x: -x[1]):
        log(f"    {top}: {count}")

    by_freq = {}
    for ind in indicators:
        freq = ind.get('frequency', '未知')
        by_freq[freq] = by_freq.get(freq, 0) + 1
    log(f"\n  按频率:")
    for freq, count in sorted(by_freq.items(), key=lambda x: -x[1]):
        log(f"    {freq}: {count}")

    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, 'r', encoding='utf-8') as f:
            progress = json.load(f)
        log(f"\n  已完成子分类: {len(progress.get('completed_categories', []))}")


def main():
    parser = argparse.ArgumentParser(description="导出/搜索 EDB宏观指标字典（并发版）")
    parser.add_argument('--all', action='store_true', help='导出全部分类')
    parser.add_argument('--search', type=str, help='搜索关键词')
    parser.add_argument('--freq', type=str, help='频率过滤: 季/月/年/日')
    parser.add_argument('--top', type=int, default=15, help='返回条数(默认15, 0=全部)')
    parser.add_argument('--categories', nargs='+', help='指定顶层分类ID')
    parser.add_argument('--subcats', nargs='+', help='直接指定子分类ID (如 00020019 00020020)')
    parser.add_argument('--stats', action='store_true', help='查看统计')
    parser.add_argument('--build-db', action='store_true', help='构建SQLite+FTS5索引(毫秒级搜索)')
    parser.add_argument('--workers', type=int, default=8, help='并发线程数(默认8)')
    args = parser.parse_args()

    if args.build_db:
        build_sqlite_db()
        return
    if args.search:
        local_search(args.search, freq=args.freq, top_n=args.top)
        return
    if args.stats:
        show_stats()
        return

    if args.categories:
        categories = args.categories
    elif args.all:
        categories = list(TOP_CATEGORIES.keys())
    else:
        categories = DEFAULT_CATEGORIES

    cat_names = [TOP_CATEGORIES.get(c, c) for c in categories]
    log(f"将导出: {', '.join(cat_names)}")
    if args.subcats:
        log(f"额外子分类: {', '.join(args.subcats)}")
    log(f"并发线程: {args.workers}")
    log(f"输出: {OUTPUT_JSON}")
    log(f"断点续传: 已启用\n")

    # 1. 获取cookies
    cookies = get_browser_cookies()
    if not cookies:
        return

    # 2. 并发导出
    start_time = time.time()
    indicators = export_tree_concurrent(
        cookies, categories, max_workers=args.workers,
        extra_subcats=args.subcats
    )
    elapsed = time.time() - start_time

    save_results(indicators)
    log(f"\n总耗时: {elapsed:.0f}秒")
    log("\n=== 导出完成 ===")
    log(f"python export_edb_full.py --search PPI")
    log(f"python export_edb_full.py --search 房地产")


if __name__ == "__main__":
    main()
