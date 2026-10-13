from local.terrain import curve as first
from local import terrain, procedural
from local import terrain as t
a=curve(0.5, __unique__=True)
b=t.curve(0.5, __unique__=True)
c=first(0.5)
d=t.curve(0.5, __unique__=False)
output("Geometry", point(vector(a+b+c+d, 0.0, 0.0)))
