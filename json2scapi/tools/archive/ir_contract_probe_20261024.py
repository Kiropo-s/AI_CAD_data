import copy,json,sys,math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from json2scapi.codegen.api import generate
from json2scapi.ir.validation import validate_ir
from json2scapi.evaluation.metrics import bbox
from json2scapi.adapters.deepcad import convert

def build(ir):
    ns={};exec(generate(ir,"synthetic_probe"),ns);model=ns["build"]();return model,ns

base=json.loads(Path("json2scapi/examples/line_arc_plate.ir.json").read_text(encoding="utf-8"))
for delta in (5e-7,5e-4):
    ir=copy.deepcopy(base);ir["tolerance"]["length"]=1e-3
    ir["entities"]["curve_demo_l1"]["params"]["end"][0]-=delta
    errors=validate_ir(ir)
    try:
        model,_=build(ir);out={"built":True,"volume":model.get_volume()}
    except Exception as error:out={"built":False,"error":str(error)[-550:]}
    print("ENDPOINT_GAP",json.dumps({"gap_mm":delta,"validation_errors":errors,**out}))

ir=copy.deepcopy(base);ir["entities"]["curve_demo_a1"]["params"]["mid"][0]+=0.0005
m0,_=build(base);m1,_=build(ir)
print("ARC_MID",json.dumps({"validation_errors":validate_ir(ir),"original_volume":m0.get_volume(),"edited_volume":m1.get_volume(),"original_bbox":bbox(m0.wrapped),"edited_bbox":bbox(m1.wrapped),"edited_mid":ir["entities"]["curve_demo_a1"]["params"]["mid"]}))

ir=json.loads(Path("json2scapi/examples/deepcad_00000007.ir.json").read_text(encoding="utf-8"))
ents=ir["entities"];profile=next(n for n in ents.values() if n["op"]=="sketch.profile");cid=next(k for k,n in ents.items() if n["op"]=="sketch.circle")
ents[cid]["params"]={"center":[0,0],"radius":100};theta=math.pi/128;center=[98.99*math.cos(theta),98.99*math.sin(theta)]
ents["inner_circle"]={"type":"SketchCircle","op":"sketch.circle","inputs":[],"params":{"center":center,"radius":1}}
ents["inner_loop"]={"type":"SketchLoop","op":"sketch.loop","inputs":["inner_circle"],"params":{"closed":True,"is_outer":False}}
profile["inputs"].append("inner_loop");profile["params"]["regions"][0]["holes"].append("inner_loop")
print("CIRCLE_NESTING",json.dumps({"true_clearance_mm":100-math.hypot(*center)-1,"validation_errors":validate_ir(ir)}))

raw=json.loads(Path("datasets/DeepCAD/cad_json/0000/00000007.json").read_text(encoding="utf-8"));f=next(n for n in raw["entities"].values() if n["type"]=="ExtrudeFeature")
f["extent_type"]="TwoSidesFeatureExtentType";f["extent_one"]["distance"]["value"]=0.02;f["extent_two"]["distance"]["value"]=0.01;f["extent_one"]["taper_angle"]["value"]=0.1;f["extent_two"]["taper_angle"]["value"]=0
ir=convert(raw);params=ir["entities"][ir["results"][0]]["params"]
print("TWO_TAPERS",json.dumps({"source_taper_radians":[0.1,0],"validation_errors":validate_ir(ir),"ir_params":params}))
