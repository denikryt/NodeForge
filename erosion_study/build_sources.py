"""Generate explicit NodeForge source for three documented erosion discretizations."""
from pathlib import Path
ROOT=Path(__file__).resolve().parent
DIRS=[('l',-1,0),('r',1,0),('d',0,-1),('u',0,1)]
D8=DIRS+[('sw',-1,-1),('se',1,-1),('nw',-1,1),('ne',1,1)]
OPP={'l':'r','r':'l','d':'u','u':'d'}

def attribute(name,typ='Float'):
    """Read one named field with Blender's native node."""
    dtype='INT' if typ=='Int' else 'FLOAT'
    return f'node("GeometryNodeInputNamedAttribute", inputs={{"Name": "{name}"}}, props={{"data_type": "{dtype}"}}, output="Attribute", typ={typ})'

def snapshot(lines,var,name,geo='geo',indent='    ',typ='Float',idx='index()'):
    """Freeze a field on a particular geometry snapshot before subsequent writes."""
    lines.append(f'{indent}{var} = sample_index({geo}, {attribute(name,typ)}, {idx}, clamp=True)')

def store(lines,name,value,geo='geo',indent='    ',typ='FLOAT'):
    """Materialize a stage so later indexed reads see the intended state."""
    lines.append(f'{indent}{geo} = store_named_attribute({geo}, "{name}", {value}, domain="POINT", type="{typ}")')

def idx(dx,dy):
    """Express a neighbour index in the detected grid ordering."""
    return f'i + ({dx}) * xs + ({dy}) * ys'

def valid(dx,dy):
    """Mask cross-row sampling and outer-boundary neighbours."""
    tests=[]
    if dx<0:tests.append('x > 0')
    if dx>0:tests.append('x < n - 1')
    if dy<0:tests.append('y > 0')
    if dy>0:tests.append('y < n - 1')
    return ' and '.join('('+a+')' for a in tests)

BASE='''# Common initial terrain, identical between all three approaches.
from packages import math
requested_n = input_int("Resolution", default=96)
requested_steps = input_int("Steps", default=500)
size = input_float("Size", default=10.0)
n = requested_n if requested_n >= 3 else 3
steps = requested_steps if requested_steps >= 0 else 0
dx = math.max(size, 0.01) / (n - 1)
area = dx * dx
geo = node("GeometryNodeMeshGrid", inputs={"Size X": size, "Size Y": size, "Vertices X": n, "Vertices Y": n}, output="Mesh", typ=Geometry)
p0 = position()
noise0 = math.noise(p0 + vector(37.13, -19.71, 13.7), scale=0.65, detail=2.5, roughness=0.5, normalize=True)
noise1 = math.noise(p0 + vector(-11.7, 83.2, 5.4), scale=0.22, detail=2.0, roughness=0.5, normalize=True)
h0 = 1.0 + 4.0 * math.exp(-(p0.x * p0.x + p0.y * p0.y) * 0.075) + 0.12 * (5.0 - p0.y) + (noise0 - 0.5) * 0.7 + (noise1 - 0.5) * 1.5
geo = store_named_attribute(geo, "height", h0, domain="POINT", type="FLOAT")
geo = store_named_attribute(geo, "initial_height", h0, domain="POINT", type="FLOAT")
a0 = sample_index(geo, position().x, 0)
a1 = sample_index(geo, position().x, 1)
xs = 1 if a1 != a0 else n
ys = n if a1 != a0 else 1
'''
FINISH='''
hfinal = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index())
initial = sample_index(geo, node("GeometryNodeInputNamedAttribute", inputs={"Name": "initial_height"}, props={"data_type": "FLOAT"}, output="Attribute", typ=Float), index())
geo = store_named_attribute(geo, "height_change", hfinal - initial, domain="POINT", type="FLOAT")
pfinal = position()
geo = set_position(geo, vector(pfinal.x, pfinal.y, hfinal))
geo = node("GeometryNodeSetShadeSmooth", inputs={"Mesh": geo, "Shade Smooth": True}, props={"domain": "FACE"}, output="Mesh", typ=Geometry)
geo = set_material(geo, "Erosion Terrain")
output("Geometry", geo)
'''

def hydraulic():
    """Virtual-pipe water flux plus conservative upwind sediment transport."""
    lines=[BASE,'''dt = math.clamp(input_float("Time Step", default=0.02), 0.0001, 0.1)
rain = math.max(input_float("Rain", default=0.03), 0.0)
evap = math.max(input_float("Evaporation", default=0.03), 0.0)
capacity_k = math.max(input_float("Capacity", default=2.0), 0.0)
erosion_k = math.max(input_float("Erosion Rate", default=0.35), 0.0)
deposition_k = math.max(input_float("Deposition Rate", default=1.0), 0.0)
open_edges = input_bool("Open Edges", default=True)
''']
    for name in ['water','sediment','f_l','f_r','f_d','f_u','water_export','sediment_export','evap_total','erosion_total','deposition_total']:
        store(lines,name,'0.0',indent='')
    lines+=['for tick in repeat_range(steps):','    i = index()','    x = (i // xs) % n','    y = (i // ys) % n']
    for var,name in [('h','height'),('w_old','water'),('s_old','sediment')]:snapshot(lines,var,name)
    lines+=['    w = w_old + rain * dt','    head = h + w']
    for name,x,y in DIRS:
        snapshot(lines,'hn_'+name,'height',idx=idx(x,y));snapshot(lines,'wn_'+name,'water',idx=idx(x,y))
        snapshot(lines,'oldf_'+name,'f_'+name)
        lines += [f'    valid_{name} = {valid(x,y)}',f'    head_{name} = hn_{name} + wn_{name} + rain * dt if valid_{name} else (0.0 if open_edges else head)',f'    trial_{name} = math.max(oldf_{name} + dt * 9.81 * area / dx * (head - head_{name}), 0.0)']
    lines+=['    total_trial = trial_l + trial_r + trial_d + trial_u','    limiter = math.min(1.0, w * area / math.max(dt * total_trial, 0.000000001))']
    for name,_,_ in DIRS:store(lines,'f_'+name,'trial_'+name+' * limiter')
    lines+=['    flux_geo = geo']
    for name,x,y in DIRS:
        snapshot(lines,'out_'+name,'f_'+name,geo='flux_geo')
        snapshot(lines,'incoming_'+name,'f_'+OPP[name],geo='flux_geo',idx=idx(x,y))
        lines += [f'    in_{name} = incoming_{name} if valid_{name} else 0.0']
    lines+=['    w_flow = math.max(w + dt / area * (in_l + in_r + in_d + in_u - out_l - out_r - out_d - out_u), 0.0)', '    vx = (in_l - out_l + out_r - in_r) / math.max(dx * (w + w_flow), 0.000001)', '    vy = (in_d - out_d + out_u - in_u) / math.max(dx * (w + w_flow), 0.000001)', '    speed = math.sqrt(vx * vx + vy * vy)', '    slope_x = ((hn_r if valid_r else h) - (hn_l if valid_l else h)) / (2.0 * dx)', '    slope_y = ((hn_u if valid_u else h) - (hn_d if valid_d else h)) / (2.0 * dx)', '    slope = math.sqrt(slope_x * slope_x + slope_y * slope_y)', '    capacity = capacity_k * speed * slope * w_flow', '    lowest = math.min(math.min(hn_l if valid_l else h, hn_r if valid_r else h), math.min(hn_d if valid_d else h, hn_u if valid_u else h))', '    erosion_limit = math.max(h - lowest, 0.0) * 0.25', '    eroded = math.min(math.max(capacity - s_old, 0.0) * math.min(erosion_k * dt, 1.0), math.min(erosion_limit, math.max(h, 0.0)))', '    deposited = math.max(s_old - capacity, 0.0) * math.min(deposition_k * dt, 1.0)', '    h_new = h - eroded + deposited', '    s_exchange = s_old + eroded - deposited', '    concentration = s_exchange / math.max(w, 0.000001)']
    store(lines,'concentration','concentration')
    lines+=['    transport_geo = geo']
    for name,x,y in DIRS:
        snapshot(lines,'cn_'+name,'concentration',geo='transport_geo',idx=idx(x,y))
        lines += [f'    sin_{name} = in_{name} * cn_{name} if valid_{name} else 0.0']
    lines+=['    s_new = math.max(s_exchange + dt / area * (sin_l + sin_r + sin_d + sin_u - concentration * (out_l + out_r + out_d + out_u)), 0.0)', '    lost_water = dt / area * ((out_l if not valid_l else 0.0) + (out_r if not valid_r else 0.0) + (out_d if not valid_d else 0.0) + (out_u if not valid_u else 0.0))', '    lost_sediment = lost_water * concentration', '    evaporated = w_flow * math.min(evap * dt, 1.0)']
    # Freeze accumulators on the current geometry before writing final-stage state.
    for name in ['water_export','sediment_export','evap_total','erosion_total','deposition_total']:snapshot(lines,'prev_'+name,name)
    for name,value in [('height','h_new'),('water','w_flow - evaporated'),('sediment','s_new'),('water_export','prev_water_export + lost_water'),('sediment_export','prev_sediment_export + lost_sediment'),('evap_total','prev_evap_total + evaporated'),('erosion_total','prev_erosion_total + eroded'),('deposition_total','prev_deposition_total + deposited'),('speed','speed')]:store(lines,name,value)
    lines+=[FINISH]
    return '\n'.join(lines)

def stream_power():
    """D8 routing, iterative upstream area accumulation, bounded explicit incision."""
    lines=[BASE.replace('default=500','default=24'),'''routing_passes = input_int("Routing Passes", default=160)
dt = math.max(input_float("Time Step", default=0.2), 0.0)
k = math.max(input_float("Erodibility", default=0.12), 0.0)
''','for tick in repeat_range(steps):','    i = index()','    x = (i // xs) % n','    y = (i // ys) % n']
    snapshot(lines,'h','height')
    lines+=['    best_slope = 0.0','    receiver = i']
    for name,x,y in D8:
        snapshot(lines,'hn_'+name,'height',idx=idx(x,y))
        distance='dx * 1.4142135623730951' if x*y else 'dx'
        lines += [f'    slope_{name} = math.max((h - hn_{name}) / ({distance}), 0.0) if {valid(x,y)} else 0.0',f'    receiver = ({idx(x,y)}) if slope_{name} > best_slope else receiver',f'    best_slope = slope_{name} if slope_{name} > best_slope else best_slope']
    lines+=['    boundary = (x == 0) or (y == 0) or (x == n - 1) or (y == n - 1)','    receiver = i if boundary else receiver']
    store(lines,'receiver','receiver',typ='INT');store(lines,'slope','best_slope');store(lines,'drainage_area','area')
    lines+=['    for route_step in repeat_range(routing_passes):','        j = index()','        xx = (j // xs) % n','        yy = (j // ys) % n']
    terms=[]
    for name,x,y in D8:
        index=idx(x,y).replace('i +','j +')
        cond=valid(x,y).replace('x ','xx ').replace('y ','yy ')
        snapshot(lines,'rec_'+name,'receiver',indent='        ',typ='Int',idx=index)
        snapshot(lines,'area_'+name,'drainage_area',indent='        ',idx=index)
        lines+=[f'        contribution_{name} = area_{name} if ({cond}) and (rec_{name} == j) else 0.0']
        terms.append('contribution_'+name)
    lines+=['        accumulated = area + '+' + '.join(terms)]
    store(lines,'drainage_area','accumulated',indent='        ')
    snapshot(lines,'a','drainage_area');snapshot(lines,'rec','receiver',typ='Int');snapshot(lines,'slope_now','slope')
    snapshot(lines,'receiver_h','height',idx='rec')
    lines+=['    incision = math.min(k * math.sqrt(a) * slope_now * dt, math.max(h - receiver_h, 0.0) * 0.2) if not boundary else 0.0']
    store(lines,'height','h - incision')
    lines+=[FINISH]
    return '\n'.join(lines)

def stream_power_mfd():
    """Partition runoff between all downhill D8 neighbours using slope weights."""
    lines=[BASE.replace('default=500','default=24'),'''routing_passes = input_int("Routing Passes", default=512)
dt = math.max(input_float("Time Step", default=0.2), 0.0)
k = math.max(input_float("Erodibility", default=0.12), 0.0)
''','for tick in repeat_range(steps):','    i = index()','    x = (i // xs) % n','    y = (i // ys) % n']
    snapshot(lines,'h','height')
    lines+=['    boundary = (x == 0) or (y == 0) or (x == n - 1) or (y == n - 1)']
    for name,x,y in D8:
        snapshot(lines,'hn_'+name,'height',idx=idx(x,y))
        distance='dx * 1.4142135623730951' if x*y else 'dx'
        lines += [f'    slope_{name} = math.max((h - hn_{name}) / ({distance}), 0.0) if ({valid(x,y)}) and (not boundary) else 0.0']
    lines += ['    slope_sum = '+' + '.join('slope_'+d[0] for d in D8)]
    for name,x,y in D8:store(lines,'p_'+name,f'slope_{name} / math.max(slope_sum, 0.000001)')
    slope=' + '.join(f'slope_{d[0]} * slope_{d[0]} / math.max(slope_sum, 0.000001)' for d in D8)
    store(lines,'slope',slope)
    lines+=['    lowest = h']
    for name,x,y in D8:lines += [f'    lowest = math.min(lowest, hn_{name}) if {valid(x,y)} else lowest']
    store(lines,'drop_limit','math.max(h - lowest, 0.0) * 0.2');store(lines,'drainage_area','area')
    lines+=['    for route_step in repeat_range(routing_passes):','        j = index()','        xx = (j // xs) % n','        yy = (j // ys) % n']
    terms=[]
    opposite={'l':'r','r':'l','d':'u','u':'d','sw':'ne','se':'nw','nw':'se','ne':'sw'}
    for name,x,y in D8:
        index=idx(x,y).replace('i +','j +')
        cond=valid(x,y).replace('x ','xx ').replace('y ','yy ')
        snapshot(lines,'weight_'+name,'p_'+opposite[name],indent='        ',idx=index)
        snapshot(lines,'area_'+name,'drainage_area',indent='        ',idx=index)
        lines+=[f'        contribution_{name} = area_{name} * weight_{name} if {cond} else 0.0']
        terms.append('contribution_'+name)
    lines+=['        accumulated = area + '+' + '.join(terms)]
    store(lines,'drainage_area','accumulated',indent='        ')
    snapshot(lines,'a','drainage_area');snapshot(lines,'slope_now','slope');snapshot(lines,'drop','drop_limit')
    lines+=['    incision = math.min(k * math.sqrt(a) * slope_now * dt, drop) if not boundary else 0.0']
    store(lines,'height','h - incision')
    lines+=[FINISH]
    return '\n'.join(lines)

def normalize_inputs(source):
    """Keep input declarations as complete assignment RHS, as required by NodeForge."""
    import ast
    result=[]
    serial=0
    for line in source.splitlines():
        if 'input_' in line and '= math.' in line:
            tree=ast.parse(line)
            call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id.startswith('input_'))
            text=ast.get_source_segment(line,call)
            temp=f'parameter_{serial}'
            serial+=1
            result.append(f'{temp} = {text}')
            line=line.replace(text,temp)
        result.append(line)
    return '\n'.join(result)+'\n'

if __name__=='__main__':
    (ROOT/'hydraulic_grid.nf').write_text(normalize_inputs(hydraulic()))
    (ROOT/'stream_power_d8.nf').write_text(normalize_inputs(stream_power()))

    (ROOT/'stream_power_mfd.nf').write_text(normalize_inputs(stream_power_mfd()))
