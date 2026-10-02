import json, re, collections, os
os.chdir(r"E:\Users\Desktop\zvi\Google涂鸦全集")
t = open("doodles_data.js", encoding="utf-8").read()
d = json.loads(t[t.index("["):].rstrip().rstrip(";"))

print("=== 前 25 条带日期的记录 ===")
n = 0
for x in d:
    if x.get("date"):
        print(f"  {x['slug'][:46]:46s} | {x['date']:22s} | {x.get('dateISO')}")
        n += 1
        if n >= 25:
            break

print()
print("=== 年份分布（dateISO）===")
yr = collections.Counter()
for x in d:
    iso = x.get("dateISO") or ""
    m = re.match(r"^(\d{4})-", iso)
    if m:
        yr[int(m.group(1))] += 1
print(sorted(yr.items())[:12], "...", sorted(yr.items())[-12:])
print("年份<1990 的条数:", sum(c for y, c in yr.items() if y < 1990))
print("年份>2030 的条数:", sum(c for y, c in yr.items() if y > 2030))

print()
print("=== date 与 slug 中数字年份不一致的例子 ===")
bad = 0
for x in d:
    iso = x.get("dateISO") or ""
    m = re.match(r"^(\d{4})-", iso)
    if not m:
        continue
    y = int(m.group(1))
    sm = re.findall(r"(19\d\d|20\d\d)", x["slug"])
    if sm and y not in [int(v) for v in sm]:
        bad += 1
        if bad <= 15:
            print(f"  {x['slug'][:52]:52s} iso={iso} slug年份={sm}")
print("  合计:", bad)
