# -*- coding:utf-8 -*-
"""
导出Choice截面数据(CSS)沪深京股票指标字典。
独立脚本，不修改任何现有数据库或代码。

输出: css_indicator_dict.json
"""
import json, time, os, sys
sys.stdout.reconfigure(line_buffering=True)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
USER_DATA_DIR = os.path.join(BASE_DIR, "browser_profile")
EXPLORE_DIR = os.path.join(BASE_DIR, "_explore_stock")
OUTPUT_JSON = os.path.join(BASE_DIR, "css_indicator_dict.json")
CATEGORY_CACHE = os.path.join(EXPLORE_DIR, "003_Command_Css_GetIndicatorCategoryInfos.json")

API_BASE = "https://quantapi.eastmoney.com/Command/Css"

# 沪深京股票的顶层ID
TARGET_ROOT = "101001"  # 沪深京股票指标


def get_cookies():
    """启动浏览器获取cookies后立即关闭"""
    from playwright.sync_api import sync_playwright
    print("获取cookies...")
    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            USER_DATA_DIR, headless=False,
            viewport={"width": 1400, "height": 900}, locale="zh-CN",
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://quantapi.eastmoney.com/Cmd/ChoiceSerialSection?from=web",
                   wait_until="networkidle", timeout=30000)
        time.sleep(2)

        import requests
        cookies = {}
        for c in context.cookies():
            cookies[c['name']] = c['value']
        context.close()
    print(f"cookies获取完毕 ({len(cookies)}个)\n")
    return cookies


def load_category_tree(session):
    """加载分类树，优先用缓存"""
    if os.path.exists(CATEGORY_CACHE):
        print(f"从缓存加载分类树: {CATEGORY_CACHE}")
        with open(CATEGORY_CACHE, 'r', encoding='utf-8') as f:
            return json.load(f)

    print("从API获取分类树...")
    resp = session.get(f"{API_BASE}/GetIndicatorCategoryInfos", timeout=30)
    data = resp.json()
    os.makedirs(EXPLORE_DIR, exist_ok=True)
    with open(CATEGORY_CACHE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def export_stock_indicators():
    import requests

    cookies = get_cookies()
    session = requests.Session()
    session.cookies.update(cookies)
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
        'Referer': 'https://quantapi.eastmoney.com/Cmd/ChoiceSerialSection?from=web',
    })

    # 加载分类树
    tree = load_category_tree(session)
    id_map = {d['id']: d for d in tree}
    all_ids = set(d['id'] for d in tree)
    all_pids = set(d['pId'] for d in tree)
    leaf_ids = all_ids - all_pids

    def get_path(nid):
        parts = []
        while nid in id_map:
            parts.insert(0, id_map[nid]['name'])
            nid = id_map[nid]['pId']
        return ' / '.join(parts)

    # 找沪深京股票下的所有叶子分类
    def is_descendant(nid, root_id):
        """判断nid是否是root_id的后代"""
        visited = set()
        cur = nid
        while cur in id_map and cur not in visited:
            visited.add(cur)
            if cur == root_id:
                return True
            cur = id_map[cur]['pId']
        return False

    stock_leaves = [lid for lid in sorted(leaf_ids) if is_descendant(lid, TARGET_ROOT)]
    print(f"沪深京股票叶子分类: {len(stock_leaves)} 个\n")

    # 逐个叶子获取指标
    all_indicators = []
    api_calls = 0
    errors = 0
    t0 = time.time()

    for i, lid in enumerate(stock_leaves, 1):
        leaf_path = get_path(lid)
        url = f"{API_BASE}/GetLeafNodeOnIndicatorCategoryInfoBy?id={lid}"

        try:
            resp = session.get(url, timeout=15)
            api_calls += 1
            if resp.ok:
                data = resp.json()
                if isinstance(data, list):
                    for item in data:
                        e = item.get('Entity', {})
                        if not e:
                            continue
                        params = e.get('Params', [])
                        desc = e.get('Desc') or {}

                        all_indicators.append({
                            'id': e.get('Id', ''),
                            'name': e.get('IndicatorName', ''),
                            'eng_name': e.get('EngName', ''),
                            'scope': e.get('Scope', ''),
                            'category_path': leaf_path,
                            'category_id': lid,
                            'unit': (desc.get('Unit') or '').strip(),
                            'definition': (desc.get('Definition') or '').strip(),
                            'algorithm': (desc.get('Algorithm') or '').strip(),
                            'source': (desc.get('Source') or '').strip(),
                            'params': [{
                                'name': p.get('Name', ''),
                                'description': p.get('Description', ''),
                                'data_type': p.get('DataType', ''),
                                'default_value': p.get('DefaultValue', ''),
                                'type_name': p.get('TypeName', ''),
                                'custom_values': p.get('CustomValues'),
                            } for p in params],
                        })

                    if i % 50 == 0 or i == len(stock_leaves):
                        elapsed = time.time() - t0
                        print(f"  [{i}/{len(stock_leaves)}] {len(all_indicators)}个指标, "
                              f"{api_calls}次API, {elapsed:.0f}秒")
                else:
                    errors += 1
            else:
                errors += 1
        except Exception as ex:
            errors += 1
            if errors <= 5:
                print(f"  ✗ {lid}: {ex}")

        time.sleep(0.05)  # 礼貌延迟

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"导出完成!")
    print(f"  指标总数: {len(all_indicators)}")
    print(f"  API调用: {api_calls}")
    print(f"  错误: {errors}")
    print(f"  耗时: {elapsed:.0f}秒")

    # 去重(按eng_name + category_id)
    seen = set()
    unique = []
    for ind in all_indicators:
        key = f"{ind['eng_name']}_{ind['category_id']}"
        if key not in seen:
            seen.add(key)
            unique.append(ind)
    print(f"  去重后: {len(unique)} (原{len(all_indicators)})")

    # 保存
    with open(OUTPUT_JSON, 'w', encoding='utf-8') as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)
    size_mb = os.path.getsize(OUTPUT_JSON) / 1024 / 1024
    print(f"  保存到: {OUTPUT_JSON} ({size_mb:.1f} MB)")

    # 统计
    param_count_dist = {}
    for ind in unique:
        n = len(ind['params'])
        param_count_dist[n] = param_count_dist.get(n, 0) + 1
    print(f"\n参数数量分布: {dict(sorted(param_count_dist.items()))}")

    # 按大类统计
    by_cat = {}
    for ind in unique:
        parts = ind['category_path'].split(' / ')
        cat = parts[2] if len(parts) > 2 else parts[-1]
        by_cat[cat] = by_cat.get(cat, 0) + 1
    print(f"\n按大类:")
    for cat, count in sorted(by_cat.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")


if __name__ == "__main__":
    export_stock_indicators()
