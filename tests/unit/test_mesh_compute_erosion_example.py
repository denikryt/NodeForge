"""Check the supplied GN erosion equations against the independent NumPy example."""

from array import array
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import math

import numpy as np
import pytest

from NodeForge.mesh_compute import MeshInputs, normalize_mesh_result, parse_mesh_compute_source

EXAMPLE = Path(__file__).resolve().parents[2] / 'examples/mesh_compute_erosion/erosion.py'
spec = spec_from_file_location('test_local_example_erosion', EXAMPLE)
module = module_from_spec(spec)
spec.loader.exec_module(module)


def _reference_one_step(source_height, n, *, dt=0.02, rain=0.03, evap=0.03,
                        capacity_k=2., erosion_k=.35, deposition_k=1.,
                        open_edges=True, talus=1.2, thermal_rate=1.,
                        outlet_level=False, size=10.):
    """Translate the source GN per-point expressions using Python scalars.

    This intentionally does not reuse the NumPy implementation, array shifts,
    masks or vectorized intermediate buffers. All intermediate updates occur in
    the three corresponding GN repeat subpasses.
    """
    dx = max(size, .01) / (n - 1)
    area = dx * dx
    h = list(source_height)
    w = [0.] * (n * n)
    s = [0.] * (n * n)
    directions = {'l': (-1,0), 'r': (1,0), 'd':(0,-1), 'u':(0,1)}
    inv = {'l':'r','r':'l','d':'u','u':'d',
           'sw':'ne','se':'nw','nw':'se','ne':'sw'}
    flux = {a:[0.] * (n*n) for a in directions}

    def valid(x,y):
        """Return whether a neighbor is inside the GN square grid."""
        return 0 <= x < n and 0 <= y < n

    def sample(values, x,y):
        """Use GN Sample Index with clamp=True for a flattened x-fast grid."""
        return values[max(0,min(n*n-1, y*n+x))]

    out = {a:[0.] * (n*n) for a in directions}
    for i in range(n*n):
        x,y = i % n, i // n
        water = w[i] + rain*dt
        head=h[i]+water
        values={}
        for a,(ox,oy) in directions.items():
            x2,y2=x+ox,y+oy
            nbhead=(sample(h,x2,y2)+sample(w,x2,y2)+rain*dt if valid(x2,y2)
                    else (0. if outlet_level else h[i]) if open_edges else head)
            values[a] = max(flux[a][i]+dt*9.81*area/dx*(head-nbhead),0.)
        limiter=min(1.,water*area/max(dt*sum(values.values()),1e-9))
        for a in directions:out[a][i]=values[a]*limiter

    w_flow=[0.] * (n*n)
    exchanged_h=[0.] * (n*n)
    exchanged_s=[0.] * (n*n)
    concentrations=[0.] * (n*n)
    inflow={a:[0.] * (n*n) for a in directions}
    for i in range(n*n):
        x,y=i%n,i//n
        for a,(ox,oy) in directions.items():
            if valid(x+ox,y+oy):
                inflow[a][i]=sample(out[inv[a]],x+ox,y+oy)
        water=w[i]+rain*dt
        w_flow[i]=max(water+dt/area*(sum(inflow[a][i] for a in directions)-sum(out[a][i] for a in directions)),0.)
        den=max(dx*(water+w_flow[i]),1e-6)
        vx=(inflow['l'][i]-out['l'][i]+out['r'][i]-inflow['r'][i])/den
        vy=(inflow['d'][i]-out['d'][i]+out['u'][i]-inflow['u'][i])/den
        speed=math.hypot(vx,vy)
        nh={a:(sample(h,x+ox,y+oy) if valid(x+ox,y+oy) else h[i]) for a,(ox,oy) in directions.items()}
        slope=math.hypot((nh['r']-nh['l'])/(2*dx),(nh['u']-nh['d'])/(2*dx))
        cap=capacity_k*speed*slope*w_flow[i]
        limit=max(h[i]-min(nh.values()),0.)*.25
        eroded=min(max(cap-s[i],0.)*min(erosion_k*dt,1.),min(limit,max(h[i],0.)))
        deposited=max(s[i]-cap,0.)*min(deposition_k*dt,1.)
        exchanged_h[i]=h[i]-eroded+deposited
        exchanged_s[i]=s[i]+eroded-deposited
        concentrations[i]=exchanged_s[i]/max(water,1e-6)

    new_s=[0.]*(n*n)
    new_w=[0.]*(n*n)
    for i in range(n*n):
        x,y=i%n,i//n
        incoming=sum(inflow[a][i]*sample(concentrations,x+ox,y+oy)
                     for a,(ox,oy) in directions.items() if valid(x+ox,y+oy))
        new_s[i]=max(exchanged_s[i]+dt/area*(incoming-concentrations[i]*sum(out[a][i] for a in directions)),0.)
        new_w[i]=w_flow[i]*(1-min(evap*dt,1.))

    dirs={**directions,'sw':(-1,-1),'se':(1,-1),'nw':(-1,1),'ne':(1,1)}
    thermal={a:[0.]*(n*n) for a in dirs}
    for i in range(n*n):
        x,y=i%n,i//n
        for a,(ox,oy) in dirs.items():
            if valid(x+ox,y+oy):
                distance=dx*(math.sqrt(2.) if ox and oy else 1.)
                thermal[a][i]=max(exchanged_h[i]-sample(exchanged_h,x+ox,y+oy)-talus*distance,0.)*thermal_rate*dt/8.
    outgoing={a:[0.]*(n*n) for a in dirs}
    for i in range(n*n):
        t_sum=sum(thermal[a][i] for a in dirs)
        t_scale=min(1.,max(exchanged_h[i],0.)*.25/max(t_sum,1e-9))
        for a in dirs:outgoing[a][i]=thermal[a][i]*t_scale
    final=[0.]*(n*n)
    for i in range(n*n):
        x,y=i%n,i//n
        incoming=sum(sample(outgoing[inv[a]],x+ox,y+oy) for a,(ox,oy) in dirs.items() if valid(x+ox,y+oy))
        final[i]=exchanged_h[i]-sum(outgoing[a][i] for a in dirs)+incoming
    return final,new_w,new_s


def _inputs(n, *, order='x-fast'):
    """Generate a deterministic GN-style square grid and nonflat heights."""
    coords=[];heights=[]
    for i in range(n*n):
        x,y=(i%n,i//n) if order=='x-fast' else (i//n,i%n)
        coords.extend((float(x),float(y),0.))
        heights.append(float(np.float32(2+.06*x+.11*y+.9*(x==1 and y==1))))
    return MeshInputs(array('f',coords), {'height':array('f',heights), 'initial_height':array('f',heights)}, n*n)


@pytest.mark.parametrize('outlet,open_edges',[(False,False),(False,True),(True,True)])
@pytest.mark.parametrize('n', [3, 5])
def test_source_equations_match_numpy_single_tick(n,open_edges,outlet):
    """All hydraulic/thermal stages reproduce independent per-vertex GN expressions."""
    inputs=_inputs(n)
    kwargs={'steps':1,'n':n,'open_edges':open_edges,'outlet_level':outlet,
            'dt':0.05,'rain':0.3,'talus':0.02,'thermal_rate':1.5,'size':2.}
    got=normalize_mesh_result(module.erosion(inputs,**kwargs),vertex_count=n*n,
                              writes=('height','water','sediment'))
    h,w,s=_reference_one_step(list(inputs.point_attributes['height']),n,
                               open_edges=open_edges,outlet_level=outlet,
                               dt=kwargs['dt'],rain=kwargs['rain'],
                               talus=kwargs['talus'],thermal_rate=kwargs['thermal_rate'],size=kwargs['size'])
    for name,expected in (('height',h),('water',w),('sediment',s)):
        np.testing.assert_allclose(got.point_attributes[name],expected,rtol=2e-4,atol=5e-5)


def test_zero_steps_preserves_height_and_returns_empty_water_sediment():
    """The zero-iteration reference initializes flux but leaves terrain intact."""
    inputs=_inputs(5)
    raw=module.erosion(inputs,steps=0,n=5)
    np.testing.assert_array_equal(raw['point_attributes']['height'],inputs.point_attributes['height'])
    for name in ('water','sediment'):
        assert not np.any(raw['point_attributes'][name])


def test_bad_square_grid_is_rejected_before_computation():
    """The Local callback rejects incompatible n rather than changing vertex order."""
    inputs=_inputs(5)
    with pytest.raises(ValueError,match='square-grid'):
        module.erosion(inputs,n=4)
    with pytest.raises(ValueError,match='nonnegative'):
        module.erosion(inputs,n=5,steps=-1)


def test_decorator_and_companion_nf_have_valid_static_contract():
    """The example remains a selectable Local callable and a correct explicit import."""
    spec=parse_mesh_compute_source(EXAMPLE.read_text(),stem='erosion')
    assert spec.reads==('height','initial_height')
    assert spec.writes==('height','water','sediment')
    nf=(EXAMPLE.parent/'erosion_numpy.nf').read_text()
    assert 'from local import erosion' in nf
    assert 'geo = erosion(' in nf
    assert 'repeat_range(' not in nf
    assert 'height_change' in nf


def test_height_datum_is_removed_at_post_simulation_boundary():
    """A negative terrain seed is restored after the GN-positive-height pass."""
    n=3
    xyz=[]
    initial=[]
    for y in range(n):
        for x in range(n):
            xyz.extend((float(x),float(y),0.))
            initial.append(float(-1.5+0.1*x+0.2*y))
    datum=1.-min(initial)
    shifted=[value+datum for value in initial]
    inputs=MeshInputs(array('f',xyz),{'height':array('f',shifted),'initial_height':array('f',initial)},n*n)
    output=module.erosion(inputs,n=n,steps=0)
    np.testing.assert_allclose(output['point_attributes']['height'],initial,rtol=0,atol=3e-7)


def test_square_grid_y_fast_vertex_order_produces_equivalent_physical_result():
    """Meshes whose first two vertices run along Y use the other grid stride."""
    n=5
    a=module.erosion(_inputs(n,order='x-fast'),steps=2,n=n,size=3.,rain=.25)
    b=module.erosion(_inputs(n,order='y-fast'),steps=2,n=n,size=3.,rain=.25)
    for key in ('height','water','sediment'):
        xfast=a['point_attributes'][key].reshape(n,n)
        yfast=b['point_attributes'][key].reshape(n,n)
        np.testing.assert_allclose(xfast,yfast.T,rtol=1e-6,atol=1e-6)
