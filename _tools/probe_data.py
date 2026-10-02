import json, collections, os
os.chdir(r"E:\Users\Desktop\zvi\Google涂鸦全集")
t = open("doodles_data.js", encoding="utf-8").read()
d = json.loads(t[t.index("["):].rstrip().rstrip(";"))
print("总数:", len(d))
print("color:", collections.Counter(x.get("color") for x in d).most_common())
print()
print("format:", collections.Counter(x.get("format") for x in d).most_common())
tp = collections.Counter()
for x in d:
    tp.update(x.get("topics") or [])
print()
print("topics:", tp.most_common(24))
print()
print("无 topics 记录:", sum(1 for x in d if not x.get("topics")))
print("有 date 记录:", sum(1 for x in d if x.get("date")))
print("有 dateISO 记录:", sum(1 for x in d if x.get("dateISO")))
print()
print("样本:", json.dumps(d[0], ensure_ascii=False)[:400])
print()
print("format 为 game/slideshow/interactive 的样本:")
for x in d:
    if x.get("format") in ("game", "slideshow", "interactive", "video"):
        print("  ", x["slug"], "|", x.get("format"), "|", x.get("title"))
        if x["slug"].startswith("celebrating-p"):
            break
