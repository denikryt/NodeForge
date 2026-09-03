obj = input_object("Source")
info = obj.info(transform_space="RELATIVE", as_instance=False)
output("Geometry", info.geometry)
output("Location", info.location)
