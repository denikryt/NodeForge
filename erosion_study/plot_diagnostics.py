"""Plot actual evaluated native node fields without smoothing the data."""
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ROOT=Path(__file__).resolve().parent
hyd=np.load(ROOT/'hydraulic_grid_96_500.npz')
d8=np.load(ROOT/'stream_power_d8_96_24.npz')
mfd=np.load(ROOT/'stream_power_mfd_96_24.npz')
def raster(data,key):
    order=np.lexsort((data['coords'][:,0],data['coords'][:,1]))
    return data[key][order].reshape(96,96)
fig,axs=plt.subplots(2,4,figsize=(16,8),layout='constrained')
panels=[(hyd,'initial_height','Initial elevation','terrain',1,6),
        (hyd,'height','Hydraulic elevation','terrain',1,6),
        (d8,'height','D8 elevation','terrain',1,6),
        (mfd,'height','MFD elevation','terrain',1,6),
        (hyd,'height_change','Hydraulic height change','RdBu',-3,3),
        (hyd,'water','Hydraulic water depth','Blues',0,None),
        (d8,'drainage_area','D8 contributing area','viridis',0,17),
        (mfd,'drainage_area','MFD contributing area','viridis',0,17)]
for ax,(data,key,title,cmap,vmin,vmax) in zip(axs.flat,panels):
    im=ax.imshow(raster(data,key),origin='lower',extent=(-5,5,-5,5),
                 interpolation='nearest',cmap=cmap,vmin=vmin,vmax=vmax)
    ax.set_title(title);ax.set_xlabel('x');ax.set_ylabel('y')
    fig.colorbar(im,ax=ax,shrink=.8)
fig.suptitle('NodeForge evaluated fields • same initial terrain • 96 × 96',fontsize=16)
fig.savefig(ROOT/'diagnostic_maps.png',dpi=160)
