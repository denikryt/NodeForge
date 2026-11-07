def nf_fixture(obj: Object['Source']):
    info = obj.info(transform_space='RELATIVE', as_instance=False)
    return (info.geometry, info.location)
