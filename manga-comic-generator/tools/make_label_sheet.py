"""Builds a local HTML page for labelling panels good/bad against Bruno's approved sheet.
Nothing is uploaded anywhere. Usage: python -m tools.make_label_sheet"""
import base64
import io
import json

from PIL import Image, ImageOps

from tools.label_common import WORK, collect_panels, standard_characters


def jpeg_b64(path, side):
    im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    im.thumbnail((side, side))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return base64.b64encode(buf.getvalue()).decode()


panels = collect_panels()
bruno = standard_characters()["Bruno"]
(WORK / "panels.json").write_text(json.dumps(panels, indent=2))
data = [{"id": p["id"], "img": jpeg_b64(p["image"], 720), "cast": p["cast"]} for p in panels]
sheet, photo = jpeg_b64(bruno.sheet_image_path, 900), jpeg_b64(bruno.reference_image_path, 500)

html = """<!doctype html><meta charset=utf-8><title>Label Bruno panels</title>
<style>
body{font:15px system-ui,sans-serif;margin:0;background:#f4f1ea;color:#222}
header{position:sticky;top:0;background:#222;color:#fff;padding:10px 16px;display:flex;gap:16px;align-items:center;z-index:5;flex-wrap:wrap}
header button{padding:6px 12px;font-size:14px;cursor:pointer}
.wrap{max-width:1100px;margin:0 auto;padding:16px}
.ref{display:flex;gap:16px;background:#fff;padding:12px;border-radius:8px;margin-bottom:16px;flex-wrap:wrap}
.ref img{max-height:300px;max-width:100%;border:1px solid #ccc}
.rules li{margin:4px 0}
.card{background:#fff;border-radius:8px;padding:12px;margin:14px 0;display:flex;gap:16px;flex-wrap:wrap;border-left:8px solid #ccc}
.card.good{border-color:#2e9d4f}.card.bad{border-color:#c0392b}.card.unsure{border-color:#e0a800}
.card img{max-width:640px;width:100%;height:auto;border:1px solid #bbb}
.ctl{flex:1;min-width:260px}
.v button{padding:8px 14px;margin:0 6px 6px 0;font-size:15px;cursor:pointer;border:2px solid #999;background:#fafafa;border-radius:6px}
.v button.on.good{background:#2e9d4f;color:#fff}.v button.on.bad{background:#c0392b;color:#fff}.v button.on.unsure{background:#e0a800}
label{display:block;margin:3px 0}
textarea{width:100%;box-sizing:border-box}
</style>
<header><b>Label Bruno panels</b><span id=prog></span><button onclick="exportLabels()">Export labels.json</button>
<span style="opacity:.7">Saved automatically in this browser</span></header>
<div class=wrap>
<div class=ref><div><b>Approved sheet (the standard)</b><br><img src="data:image/jpeg;base64,__SHEET__"></div>
<div><b>Real product photo (context)</b><br><img src="data:image/jpeg;base64,__PHOTO__"></div>
<div class=rules><b>How to label (Bruno only; ignore the elephant)</b><ul>
<li><b>Good</b> = Bruno's DESIGN matches the sheet: big black oval nose, eyes, ears, heart patch, proportions, no teeth/fangs/claws/tail.</li>
<li><b>Bad</b> = any of those fixed features is off. Tick what is wrong.</li>
<li>Expression, pose, angle and mood may change &ndash; that is <i>not</i> a problem.</li>
<li><b>Unsure</b> = you can't tell; those are skipped.</li></ul></div></div>
<div id=cards></div></div>
<script>
const DATA=__DATA__;
const TAGS=[["nose","Nose wrong (small/pointy/shape)"],["teeth","Teeth / fangs / claws"],["patch","Heart patch wrong (shape/position)"],["face","Eyes / ears / face design"],["extra","Extra feature (tail, clothing...)"],["char","Extra character / person"],["text","Text drawn in art"],["body","Body proportions"]];
let L=JSON.parse(localStorage.getItem("labels")||"{}");
function save(){localStorage.setItem("labels",JSON.stringify(L));prog()}
function prog(){const n=Object.values(L).filter(x=>x.verdict).length;document.getElementById("prog").textContent=n+" / "+DATA.length+" labelled"}
function set(id,v){L[id]=L[id]||{tags:[],note:""};L[id].verdict=v;save();render()}
function tag(id,t,on){L[id]=L[id]||{tags:[],note:""};const a=L[id].tags;const i=a.indexOf(t);if(on&&i<0)a.push(t);if(!on&&i>=0)a.splice(i,1);save()}
function note(id,v){L[id]=L[id]||{tags:[],note:""};L[id].note=v;save()}
function render(){document.getElementById("cards").innerHTML=DATA.map((d,i)=>{const l=L[d.id]||{tags:[],note:""};
return `<div class="card ${l.verdict||""}"><img src="data:image/jpeg;base64,${d.img}"><div class=ctl><b>#${i+1} &nbsp;(${d.id})</b>
<div class=v><button class="good ${l.verdict=="good"?"on":""}" onclick="set('${d.id}','good')">Good</button><button class="bad ${l.verdict=="bad"?"on":""}" onclick="set('${d.id}','bad')">Bad</button><button class="unsure ${l.verdict=="unsure"?"on":""}" onclick="set('${d.id}','unsure')">Unsure</button></div>
${TAGS.map(t=>`<label><input type=checkbox ${l.tags.includes(t[0])?"checked":""} onchange="tag('${d.id}','${t[0]}',this.checked)"> ${t[1]}</label>`).join("")}
<textarea rows=2 placeholder="optional note" onchange="note('${d.id}',this.value)">${l.note||""}</textarea></div></div>`}).join("");prog()}
function exportLabels(){const b=new Blob([JSON.stringify(L,null,2)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(b);a.download="labels.json";a.click()}
render();
</script>"""
html = html.replace("__SHEET__", sheet).replace("__PHOTO__", photo).replace("__DATA__", json.dumps(data))
out = WORK / "labeler.html"
out.write_text(html)
print(out.resolve(), f"{out.stat().st_size / 1e6:.1f} MB,", len(panels), "panels")
