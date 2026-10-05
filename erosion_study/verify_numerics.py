"""Independent CPU checks of water/sediment stages and D8 drainage-area accumulation."""
from pathlib import Path
import json
from collections import deque
import numpy as np
ROOT=Path(__file__).resolve().parent


def row_order(data):
    """Sort Blender point indices into y-major raster order."""
    coords=data['coords']
    return np.lexsort((coords[:,0],coords[:,1]))


def shifted(a,dx,dy):
    """Read clipped neighbours and return a separate validity mask."""
    n=a.shape[0]
    yy,xx=np.mgrid[:n,:n]
    valid=(xx+dx>=0)&(xx+dx<n)&(yy+dy>=0)&(yy+dy<n)
    return a[np.clip(yy+dy,0,n-1),np.clip(xx+dx,0,n-1)],valid


def hydraulic_reference(initial,steps,dx,dt=.02,rain=.03,evap=0.,capacity=2.,erosion=.35,deposition=1.,open_edges=False):
    """Run the finite-volume stencil with conservative first-order upwind sediment flux."""
    h=initial.copy();w=np.zeros_like(h);s=np.zeros_like(h)
    f=[np.zeros_like(h) for _ in range(4)]
    directions=[(-1,0),(1,0),(0,-1),(0,1)]
    opposite=[1,0,3,2]
    area=dx*dx
    water_export=np.zeros_like(h);sediment_export=np.zeros_like(h)
    for _ in range(steps):
        w=w+rain*dt
        hn=[];valid=[];trial=[]
        for q,(xx,yy) in enumerate(directions):
            nh,mask=shifted(h,xx,yy);nw,_=shifted(w,xx,yy)
            hn.append(nh);valid.append(mask)
            neighbour_head=np.where(mask,nh+nw,0. if open_edges else h+w)
            trial.append(np.maximum(f[q]+dt*9.81*area/dx*(h+w-neighbour_head),0.))
        limiter=np.minimum(1.,w*area/np.maximum(dt*sum(trial),1e-9))
        f=[a*limiter for a in trial]
        incoming=[np.where(valid[q],shifted(f[opposite[q]],xx,yy)[0],0.)
                  for q,(xx,yy) in enumerate(directions)]
        wf=np.maximum(w+dt/area*(sum(incoming)-sum(f)),0.)
        vx=(incoming[0]-f[0]+f[1]-incoming[1])/np.maximum(dx*(w+wf),1e-6)
        vy=(incoming[2]-f[2]+f[3]-incoming[3])/np.maximum(dx*(w+wf),1e-6)
        sx=(np.where(valid[1],hn[1],h)-np.where(valid[0],hn[0],h))/(2*dx)
        sy=(np.where(valid[3],hn[3],h)-np.where(valid[2],hn[2],h))/(2*dx)
        cap=capacity*np.hypot(vx,vy)*np.hypot(sx,sy)*wf
        lowest=np.minimum.reduce([np.where(mask,nh,h) for nh,mask in zip(hn,valid)])
        eroded=np.minimum(np.maximum(cap-s,0)*min(erosion*dt,1),np.minimum(np.maximum(h-lowest,0)*.25,np.maximum(h,0)))
        deposited=np.maximum(s-cap,0)*min(deposition*dt,1)
        h=h-eroded+deposited
        sex=s+eroded-deposited
        c=sex/np.maximum(w,1e-6)
        incoming_s=[np.where(valid[q],incoming[q]*shifted(c,xx,yy)[0],0)
                    for q,(xx,yy) in enumerate(directions)]
        s=np.maximum(sex+dt/area*(sum(incoming_s)-c*sum(f)),0)
        exported=dt/area*sum(np.where(mask,0,fq) for mask,fq in zip(valid,f))
        water_export+=exported;sediment_export+=exported*c
        w=wf*(1-min(evap*dt,1))
    return {'height':h,'water':w,'sediment':s,'water_export':water_export,'sediment_export':sediment_export}


def drainage_reference(receiver,cell_area):
    """Accumulate upstream area once in topological order; reject receiver cycles."""
    n=len(receiver);i=np.arange(n);moving=receiver!=i
    indegree=np.bincount(receiver[moving],minlength=n)
    queue=deque(np.flatnonzero(indegree==0).tolist())
    area=np.full(n,cell_area);depth=np.ones(n,dtype=int);visited=0
    while queue:
        j=queue.popleft();visited+=1
        r=receiver[j]
        if r!=j:
            area[r]+=area[j];depth[r]=max(depth[r],depth[j]+1)
            indegree[r]-=1
            if indegree[r]==0:queue.append(int(r))
    assert visited==n,'Routing cycle'
    return area,int(depth.max()),float(area[~moving].sum())


def mfd_reference(data,cell_area):
    """Accumulate weighted downhill links in topological order."""
    n=int(np.sqrt(len(data['height'])))
    order=row_order(data)
    raster_to_source=order.reshape(n,n)
    directions=[('l',-1,0),('r',1,0),('d',0,-1),('u',0,1),
                ('sw',-1,-1),('se',1,-1),('nw',-1,1),('ne',1,1)]
    edges=[[] for _ in order];indegree=np.zeros(len(order),dtype=int)
    for name,dx,dy in directions:
        for y in range(n):
            for x in range(n):
                i=raster_to_source[y,x];weight=float(data['p_'+name][i])
                if weight>0:
                    assert 0<=x+dx<n and 0<=y+dy<n
                    j=int(raster_to_source[y+dy,x+dx])
                    edges[i].append((j,weight));indegree[j]+=1
    q=deque(np.flatnonzero(indegree==0).tolist())
    area=np.full(len(order),cell_area);depth=np.ones(len(order),dtype=int);visited=0
    for i,row in enumerate(edges):
        total=sum(w for _,w in row)
        assert total==0 or abs(total-1)<1e-5,(i,total)
    while q:
        i=q.popleft();visited+=1
        for j,w in edges[i]:
            area[j]+=area[i]*w;depth[j]=max(depth[j],depth[i]+1)
            indegree[j]-=1
            if indegree[j]==0:q.append(j)
    assert visited==len(order)
    return area,int(depth.max())

def main():
    """Compare independently computed state to actual exported Blender fields."""
    report={}
    data=np.load(ROOT/'hydraulic_grid_16_10.npz')
    order=row_order(data);n=16
    initial=data['initial_height'][order].reshape(n,n)
    expected=hydraulic_reference(initial,10,10/15)
    errors={}
    for name,value in expected.items():
        actual=data[name][order].reshape(n,n)
        errors[name]=float(np.max(np.abs(actual-value)))
        assert errors[name]<3e-5,(name,errors[name])
    report['hydraulic_cpu_reference']=errors
    routing=[]
    for n,steps in ((16,1),(96,24)):
        data=np.load(ROOT/f'stream_power_d8_{n}_{steps}.npz')
        expected,depth,sinks=drainage_reference(data['receiver'].astype(int),(10/(n-1))**2)
        error=float(np.max(np.abs(expected-data['drainage_area'])))
        assert error<1e-4,error
        assert abs(sinks-n*n*(10/(n-1))**2)<1e-8
        routing.append({'resolution':n,'steps':steps,'max_area_error':error,'max_path_cells':depth,'total_sink_area':sinks})
    report['d8_topological_reference']=routing
    data=np.load(ROOT/'stream_power_mfd_96_24.npz')
    expected,depth=mfd_reference(data,(10/95)**2)
    error=float(np.max(np.abs(expected-data['drainage_area'])))
    assert error<1e-4,error
    report['mfd_topological_reference']={'max_area_error':error,'max_path_cells':depth}
    (ROOT/'numerical_verification.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report,indent=2))

if __name__=='__main__':main()
