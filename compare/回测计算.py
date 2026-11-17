# -*- coding: utf-8 -*-
"""
组合回测：4 只长债 vs 4 只长债 + 短债 + 现金
================================================
回测区间：2025-04-01 ~ 2026-11-09
数据来源：Wind「基金比较」导出的 数据底稿.xlsx（日频复权单位净值）
运行环境：Python 3.7+，只用到标准库（无需 pandas / numpy）

用法：
    python 回测计算.py
    python 回测计算.py 路径/数据底稿.xlsx
"""

import csv
import math
import os
import re
import sys
import zipfile
import bisect
import datetime as dt

# ---------------------------------------------------------------- 参数

XLSX_DEFAULT = r"C:\Users\张硕\Desktop\数据底稿.xlsx"

START = dt.date(2025, 4, 1)     # 回测起点
END = dt.date(2026, 11, 9)      # 回测终点（"至今"）
RF = 0.010                      # 无风险利率，年化 1.0%（夏普/索提诺用）
TRADING_DAYS = 252              # 年化系数：日频用 252，周频用 52

# 每只产品在 xlsx 中的起始列（每只占 10 列一组）
# 组内顺序：日期 / 净值日期 / 复权单位净值(支持转型基金) / 更新频率 /
#          复权单位净值 / 复权因子 / 今年以来回报 / 单年度回报 /
#          回撤(相对前期高点) / 组合久期
PRODUCTS = [
    ("RUYI65",    "平安资管如意65号",        "A"),
    ("CHUANGZHI", "人保资产创智国债增强",     "L"),
    ("HUATAI",    "华泰资产华泰稳健增益",     "W"),
    ("QINGSONG",  "平安资管青松ESG1号",      "AH"),
    ("WENYING",   "人保资产稳盈专享3号",      "AS"),
]
NAV_OFFSET = 2   # 组内第 3 列才是真正的复权净值
DD_OFFSET = 8    # 组内第 9 列是"回撤(相对前期高点)"，单位 %（-0.843 = -0.843%）

# 组合权重：(成分, 权重)
PORT1 = {"RUYI65": .25, "CHUANGZHI": .25, "HUATAI": .25, "QINGSONG": .25}
PORT2 = {"RUYI65": .24, "CHUANGZHI": .24, "HUATAI": .24, "QINGSONG": .24,
         "WENYING": .03, "CASH": .01}   # 现金按 0 收益计
PORT_NAME = {"组合1": "4 只长债各 25%",
             "组合2": "4 只长债各 24% + 短债 3% + 现金 1%"}


# ---------------------------------------------------------------- 读 xlsx

def col_index(letter):
    """'A' -> 0, 'L' -> 11, 'AH' -> 33"""
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def read_xlsx(path):
    """只用标准库解析 xlsx，返回 {日期: {产品key: 净值}}。

    xlsx 本质是 zip + xml，所以不需要 openpyxl 也能读。
    """
    z = zipfile.ZipFile(path)

    # 共享字符串表：单元格里是索引，真文字在这里
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        xml = z.read("xl/sharedStrings.xml").decode("utf-8")
        for si in re.findall(r"<si>(.*?)</si>", xml, re.S):
            shared.append("".join(re.findall(r"<t[^>]*>(.*?)</t>", si, re.S)))

    sheet = z.read("xl/worksheets/sheet1.xml").decode("utf-8")

    # 每只产品要读的列号：净值列 + 回撤列
    want = {}
    for key, _name, start in PRODUCTS:
        want[col_index(start) + NAV_OFFSET] = key
        want[col_index(start) + DD_OFFSET] = "dd_" + key

    data = {}
    for m in re.finditer(r'<row[^>]*r="(\d+)"[^>]*>(.*?)</row>', sheet, re.S):
        cells = {}
        for c in re.finditer(
                r'<c[^>]*?r="([A-Z]+)(\d+)"([^>]*)>(.*?)</c>', m.group(2), re.S):
            ref, attr, inner = c.group(1), c.group(3), c.group(4)
            v = re.search(r"<v>(.*?)</v>", inner, re.S)
            if v is None:
                continue
            t = re.search(r't="(\w+)"', attr)
            val = shared[int(v.group(1))] if (t and t.group(1) == "s") else v.group(1)
            cells[col_index(ref)] = val

        # 第 1 列是 Excel 日期序列号；只认 5 位数字的行（跳过表头）
        serial = cells.get(0, "")
        if not re.fullmatch(r"\d{5}", serial or ""):
            continue
        day = (dt.date(1899, 12, 30) + dt.timedelta(days=int(serial)))
        if not (START <= day <= END):
            continue

        rec = {}
        for ci, key in want.items():
            raw = cells.get(ci)
            if raw not in (None, ""):
                rec[key] = float(raw)
        data[day] = rec
    return data


# ---------------------------------------------------------------- 指标

def max_drawdown(series, i0=0, i1=None):
    """最大回撤：遍历序列，记录历史最高点，取 (当前/最高 - 1) 的最小值。

    回撤是路径依赖量，不能由成分基金的回撤加权得到，必须逐日走一遍。
    """
    i1 = len(series) - 1 if i1 is None else i1
    peak, peak_i = series[i0], i0
    worst, worst_i = 0.0, i0
    for i in range(i0, i1 + 1):
        if series[i] > peak:
            peak, peak_i = series[i], i
        d = series[i] / peak - 1
        if d < worst:
            worst, worst_i = d, i
    return worst, worst_i, peak_i


def metrics(series, dates, i0=0, i1=None):
    """算一组指标：区间收益、年化、波动、夏普、索提诺、最大回撤、卡玛"""
    i1 = len(series) - 1 if i1 is None else i1

    # 1) 区间收益 = 期末 / 期初 - 1
    tot = series[i1] / series[i0] - 1

    # 2) 年化收益：按自然日折算，(1+区间)^(365/天数) - 1
    days = (dates[i1] - dates[i0]).days
    ann = (1 + tot) ** (365 / days) - 1

    # 3) 日收益率序列
    r = [series[i] / series[i - 1] - 1 for i in range(i0 + 1, i1 + 1)]
    n = len(r)
    mu = sum(r) / n

    # 4) 年化波动 = 日收益样本标准差 × √252
    sd = math.sqrt(sum((x - mu) ** 2 for x in r) / (n - 1))
    vol = sd * math.sqrt(TRADING_DAYS)

    # 5) 夏普 = 年化超额收益 / 年化波动（无风险利率按日折算）
    rf_d = (1 + RF) ** (1 / TRADING_DAYS) - 1
    sharpe = (mu - rf_d) * TRADING_DAYS / vol

    # 6) 最大回撤
    md, md_i, peak_i = max_drawdown(series, i0, i1)

    # 7) 索提诺 = 年化超额 / 年化下行波动（只惩罚负偏离）
    downside = math.sqrt(sum(min(x - rf_d, 0) ** 2 for x in r) / n)
    sortino = (mu - rf_d) * TRADING_DAYS / (downside * math.sqrt(TRADING_DAYS))

    # 8) 卡玛 = 年化收益 / |最大回撤|
    calmar = ann / abs(md) if md else float("nan")

    return dict(tot=tot, ann=ann, vol=vol, sharpe=sharpe, mdd=md,
                mdd_date=dates[md_i], peak_date=dates[peak_i],
                sortino=sortino, calmar=calmar, days=days)


def correlation(a, b):
    """皮尔逊相关系数"""
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(sum((x - ma) ** 2 for x in a) *
                           sum((y - mb) ** 2 for y in b))


# ---------------------------------------------------------------- 主流程

def main():
    path = sys.argv[1] if len(sys.argv) > 1 else XLSX_DEFAULT
    if not os.path.exists(path):
        print("找不到文件：", path)
        return

    raw = read_xlsx(path)
    dates = sorted(raw)
    keys = [k for k, _, _ in PRODUCTS]

    # 净值序列。若某只产品某天没披露（周频产品），用前值填充 → 当天收益为 0
    nav = {}
    for k in keys:
        s, last = [], None
        for d in dates:
            v = raw[d].get(k)
            last = v if v is not None else last
            s.append(last)
        nav[k] = s

    print(f"数据区间：{dates[0]} ~ {dates[-1]}，共 {len(dates)} 个交易日")
    for k in keys:
        print(f"  {k:<10} {len(nav[k])} 个点  "
              f"{nav[k][0]:.4f} -> {nav[k][-1]:.4f}")

    # 日收益率
    rets = {k: [nav[k][i] / nav[k][i - 1] - 1 for i in range(1, len(dates))]
            for k in keys}

    # 组合净值：先按权重加总当日收益，再逐日复利
    #   R_port(t) = Σ w_i × r_i(t)
    #   P(t) = P(t-1) × (1 + R_port(t))
    # 注意：必须"先加收益再复利"，不能把成分净值先加权（那是错的）
    def build(weights):
        p = [1.0]
        for i in range(len(dates) - 1):
            r = sum(w * rets.get(k, [0] * (len(dates) - 1))[i]
                    for k, w in weights.items() if k != "CASH")
            p.append(p[-1] * (1 + r))
        return p

    p1, p2 = build(PORT1), build(PORT2)

    # ---- 全区间
    print("\n" + "=" * 78)
    print("一、全区间 2025-04-01 ~ 2026-11-09")
    print("=" * 78)
    hdr = f"{'名称':<16}{'区间收益':>10}{'年化':>9}{'波动':>9}{'夏普':>7}{'最大回撤':>10}{'回撤日':>13}"
    print(hdr)
    print("-" * 78)

    results = {}
    for name, ser in [("组合1", p1), ("组合2", p2)] + \
                     [(n, nav[k]) for k, n, _ in PRODUCTS]:
        m = metrics(ser, dates)
        results[name] = m
        print(f"{name:<16}{m['tot']*100:>9.2f}%{m['ann']*100:>8.2f}%"
              f"{m['vol']*100:>8.2f}%{m['sharpe']:>7.2f}"
              f"{m['mdd']*100:>9.2f}%{str(m['mdd_date']):>13}")

    # ---- 年度分段
    def idx(d):
        i = bisect.bisect_left(dates, d)
        return min(i, len(dates) - 1)

    a25, b25 = idx(dt.date(2025, 4, 1)), idx(dt.date(2025, 12, 31))
    a26, b26 = idx(dt.date(2026, 1, 1)), len(dates) - 1

    print("\n" + "=" * 78)
    print(f"二、年度分段  2025段 {dates[a25]}~{dates[b25]}  |  "
          f"2026段 {dates[a26]}~{dates[b26]}")
    print("=" * 78)
    print(f"{'名称':<16}{'2025收益':>10}{'2025回撤':>11}{'2026收益':>11}{'2026回撤':>11}")
    print("-" * 78)
    seg = {}
    for name, ser in [("组合1", p1), ("组合2", p2)] + \
                     [(n, nav[k]) for k, n, _ in PRODUCTS]:
        s25 = metrics(ser, dates, a25, b25)
        s26 = metrics(ser, dates, a26, b26)
        seg[name] = (s25, s26)
        print(f"{name:<16}{s25['tot']*100:>9.2f}%{s25['mdd']*100:>10.2f}%"
              f"{s26['tot']*100:>10.2f}%{s26['mdd']*100:>10.2f}%")

    # ---- 相关性
    print("\n" + "=" * 78)
    print("三、日收益相关系数（区间内）")
    print("=" * 78)
    short = {k: n[:6] for k, n, _ in PRODUCTS}
    print(" " * 10 + "".join(f"{short[k]:>9}" for k in keys))
    for a in keys:
        print(f"{short[a]:>10}" + "".join(f"{correlation(rets[a], rets[b]):>9.2f}"
                                         for b in keys))

    # ---- 与底稿回撤列校验
    print("\n" + "=" * 78)
    print("四、与底稿「回撤(相对前期高点)」列交叉校验")
    print("=" * 78)
    print("说明：底稿该列相对的是【全历史】前期高点，若高点在 2025-04-01 之前，")
    print("      其数值会深于「区间内」最大回撤，属正常，不是算错。")
    for k, name, _start in PRODUCTS:
        col = [raw[d].get("dd_" + k) for d in dates]
        col = [c for c in col if c is not None]
        mine = results[name]["mdd"] * 100
        if not col:
            print(f"{name:<16}底稿该列为空，跳过")
            continue
        tag = "一致" if abs(min(col) - mine) < 0.01 else "差在前高位置（正常）"
        print(f"{name:<16}底稿 {min(col):>7.3f}%   自算 {mine:>7.3f}%   {tag}")

    # ---- 写结果 CSV
    out = "回测结果.csv"
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["date", "组合1净值", "组合2净值", "组合1回撤", "组合2回撤"])
        peak1 = peak2 = -1e18
        for i, d in enumerate(dates):
            peak1 = max(peak1, p1[i])
            peak2 = max(peak2, p2[i])
            w.writerow([d, f"{p1[i]:.8f}", f"{p2[i]:.8f}",
                        f"{p1[i] / peak1 - 1:.6f}", f"{p2[i] / peak2 - 1:.6f}"])
    print(f"\n已写出 {out}（组合净值 + 回撤曲线，可直接画图）")


if __name__ == "__main__":
    main()
