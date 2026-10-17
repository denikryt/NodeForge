"""NumPy implementation of hydraulic_grid_shape_cpu_fast.nf's erosion loop.

This Local callback operates on the *internal, datum-shifted* height from the
original GN prefix. The companion .nf source performs height-datum removal,
height_change, Set Position, smoothing and material assignment afterward.
The input is a square GN mesh grid in its original per-vertex index order.
"""

import numpy as np
from NodeForge import mesh_compute


@mesh_compute(reads=("height", "initial_height"), writes=("height", "water", "sediment"))
def erosion(
    mesh,
    steps: int = 500,
    n: int = 96,
    size: float = 10.0,
    dt: float = 0.02,
    rain: float = 0.03,
    evap: float = 0.03,
    capacity_k: float = 2.0,
    erosion_k: float = 0.35,
    deposition_k: float = 1.0,
    open_edges: bool = True,
    talus: float = 1.2,
    thermal_rate: float = 1.0,
    outlet_level: bool = False,
):
    """Apply hydraulic flux, sediment advection and eight-way thermal movement.

    Equations and boundary branches correspond to the source GN Repeat Zone.
    Float32 evaluation order can differ slightly from native GN; source
    terrain/noise and material creation intentionally remain in GN.
    """
    if n < 3 or n * n != mesh.vertex_count:
        raise ValueError("Erosion requires exactly n*n square-grid vertices (n >= 3)")
    if steps < 0:
        raise ValueError("Erosion steps must be nonnegative")
    dt = max(0.0001, min(0.1, dt))
    rain = max(rain, 0.0)
    evap = max(evap, 0.0)
    capacity_k = max(capacity_k, 0.0)
    erosion_k = max(erosion_k, 0.0)
    deposition_k = max(deposition_k, 0.0)
    talus = max(talus, 0.0)
    thermal_rate = max(0.0, min(10.0, thermal_rate))
    dx = max(size, 0.01) / (n - 1)
    area = dx * dx

    # The source script infers the grid indexing from the first two x values.
    xyz = np.frombuffer(mesh.positions, dtype=np.float32).reshape(n * n, 3)
    x_axis = 1 if xyz[0, 0] != xyz[1, 0] else 0
    y_axis = 1 - x_axis
    indices = np.indices((n, n))
    x, y = indices[x_axis], indices[y_axis]
    masks = {}
    for ox in (-1, 0, 1):
        for oy in (-1, 0, 1):
            masks[(ox, oy)] = ((x + ox >= 0) & (x + ox < n) &
                               (y + oy >= 0) & (y + oy < n))

    def adjacent(values, ox, oy):
        """Sample GN neighbor indices; callers mask invalid edge positions."""
        return np.roll(values, shift=(-ox, -oy), axis=(x_axis, y_axis))

    h = np.frombuffer(mesh.point_attributes["height"], dtype=np.float32).reshape(n, n).copy()
    w = np.zeros_like(h)
    s = np.zeros_like(h)
    # Original GN Repeat Zone initializes flux state once before the first tick.
    flux = {key: np.zeros_like(h) for key in ("l", "r", "d", "u")}
    direction = {"l": (-1, 0), "r": (1, 0), "d": (0, -1), "u": (0, 1)}
    reverse = {"l": "r", "r": "l", "d": "u", "u": "d"}
    dt_per_area = dt / area
    pressure = dt * 9.81 * area / dx
    erosion_gain = min(erosion_k * dt, 1.0)
    deposition_gain = min(deposition_k * dt, 1.0)
    evaporation = min(evap * dt, 1.0)
    thermal_gain = thermal_rate * dt / 8.0

    for _ in range(steps):
        water_plus_rain = w + rain * dt
        head = h + water_plus_rain
        nearby_h = {key: adjacent(h, *delta) for key, delta in direction.items()}
        nearby_w = {key: adjacent(w, *delta) for key, delta in direction.items()}
        trial = {}
        for key, delta in direction.items():
            valid = masks[delta]
            # In the original GN script a closed edge samples the local head;
            # an open edge samples h or 0 depending on Legacy Outlet.
            boundary_head = (0.0 if outlet_level else h) if open_edges else head
            neighbor_head = np.where(valid, nearby_h[key] + nearby_w[key] + rain * dt, boundary_head)
            trial[key] = np.maximum(flux[key] + pressure * (head - neighbor_head), 0.0)
        limiter = np.minimum(1.0, water_plus_rain * area /
                             np.maximum(dt * sum(trial.values()), 1e-9))
        out = {key: trial[key] * limiter for key in direction}
        inflow = {key: np.where(masks[delta], adjacent(out[reverse[key]], *delta), 0.0)
                  for key, delta in direction.items()}
        w_flow = np.maximum(water_plus_rain + dt_per_area *
                            (sum(inflow.values()) - sum(out.values())), 0.0)
        velocity_denom = np.maximum(dx * (water_plus_rain + w_flow), 1e-6)
        vx = (inflow["l"] - out["l"] + out["r"] - inflow["r"]) / velocity_denom
        vy = (inflow["d"] - out["d"] + out["u"] - inflow["u"]) / velocity_denom
        speed = np.sqrt(vx * vx + vy * vy)
        nh = {key: np.where(masks[delta], nearby_h[key], h) for key, delta in direction.items()}
        sx = (nh["r"] - nh["l"]) / (2.0 * dx)
        sy = (nh["u"] - nh["d"]) / (2.0 * dx)
        slope = np.sqrt(sx * sx + sy * sy)
        capacity = capacity_k * speed * slope * w_flow
        lowest = np.minimum.reduce(tuple(nh.values()))
        erosion_limit = np.maximum(h - lowest, 0.0) * 0.25
        eroded = np.minimum(np.maximum(capacity - s, 0.0) * erosion_gain,
                            np.minimum(erosion_limit, np.maximum(h, 0.0)))
        deposited = np.maximum(s - capacity, 0.0) * deposition_gain
        exchanged_h = h - eroded + deposited
        exchanged_s = s + eroded - deposited
        concentration = exchanged_s / np.maximum(water_plus_rain, 1e-6)
        sed_in = sum(np.where(masks[delta], inflow[key] * adjacent(concentration, *delta), 0.0)
                     for key, delta in direction.items())
        new_s = np.maximum(exchanged_s + dt_per_area *
                           (sed_in - concentration * sum(out.values())), 0.0)
        new_w = w_flow - w_flow * evaporation

        # Thermal movement includes 4 orthogonal and 4 diagonal neighbors.
        thermal_dirs = {**direction,
                        "sw": (-1, -1), "se": (1, -1),
                        "nw": (-1, 1), "ne": (1, 1)}
        thermal = {}
        for key, delta in thermal_dirs.items():
            distance = dx * (1.4142135623730951 if delta[0] and delta[1] else 1.0)
            neighbor_h = adjacent(exchanged_h, *delta)
            thermal[key] = np.where(masks[delta], np.maximum(exchanged_h - neighbor_h - talus * distance, 0.0) * thermal_gain, 0.0)
        thermal_sum = sum(thermal.values())
        thermal_scale = np.minimum(1.0, np.maximum(exchanged_h, 0.0) * 0.25 /
                                   np.maximum(thermal_sum, 1e-9))
        outgoing = {key: thermal[key] * thermal_scale for key in thermal_dirs}
        opposite = {"l": "r", "r": "l", "d": "u", "u": "d",
                    "sw": "ne", "se": "nw", "nw": "se", "ne": "sw"}
        incoming = sum(np.where(masks[delta], adjacent(outgoing[opposite[key]], *delta), 0.0)
                       for key, delta in thermal_dirs.items())
        h = exchanged_h - thermal_sum * thermal_scale + incoming
        w, s, flux = new_w, new_s, out

    # Remove the datum shift applied to the source terrain inside the GN prefix.
    # Doing so here avoids a forbidden pre-boundary GN field dependency in the suffix.
    initial = np.frombuffer(mesh.point_attributes["initial_height"], dtype=np.float32)
    initial_min = float(initial.min())
    datum = max(1.0 - initial_min, 0.0) if initial_min < 0.0 else 0.0
    return {"point_attributes": {"height": (h - datum).ravel(),
                                 "water": w.ravel(), "sediment": s.ravel()}}
